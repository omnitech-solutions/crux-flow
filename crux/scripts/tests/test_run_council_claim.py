"""The council runner's claim: the committed attempt record that council execution begins at.

Every case builds a temporary git repository with `_council_gate_support.Env` (an adr cycle book,
a run snapshot at the real layout, the fixture commit pinned as the run's `base_commit`, and one
setup commit after it), isolates git from the machine's configuration with `isolated_git_config`,
and calls `run-council.py`'s `main(argv, transport=...)` in-process with an `httpx.MockTransport`
that counts every request. No case reaches the network or reads a real key; the gateway key is a
synthetic dummy and no output carries it.

The council gate under test is prompt 3, the adr module's council prompt (module `adr-1`).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _council_gate_support as sup  # noqa: E402
import test_run_council as trc  # noqa: E402  (module import: its test classes are not re-collected here)

SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import council_records as cr  # noqa: E402

DUMMY_KEY = trc.DUMMY_KEY
PROMPT = 3
TAG = "adr-1"
QUESTION_REL = "docs/questions/council-question.md"
TYPO = "docs/adrs/ADR-0001-fixtur.md"


@unittest.skipUnless(trc.HAVE_COUNCIL, "council/router deps unavailable - run under uv")
class _Harness(unittest.TestCase):
    """A run at the adr module's council prompt, the subject revised by the run after its base."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.home = self.tmp / "crux-home"
        self.home.mkdir()
        self.env = sup.Env(self.tmp / "repo", base=True)
        self.root = self.env.root
        self.env.set_run(PROMPT)
        self.question = self.root / QUESTION_REL
        self.question.parent.mkdir(parents=True)
        self.question.write_text("Is the proposal sound? Review Completeness, Correctness, Consistency, "
                                 "Clarity and Security.\n")
        self.env.subject.write_text("# subject v1, revised by the run before the council prompt\n")
        sup.commit_all(self.root, "setup")
        self.git_cfg = sup.isolated_git_config(self.tmp / "gitcfg")
        patcher = mock.patch.dict(os.environ, {"CRUX_HOME": str(self.home), "OPENROUTER_API_KEY": DUMMY_KEY,
                                               **self.git_cfg})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rc = trc.rc_mod

    # -- running ------------------------------------------------------------------------------

    def argv(self, round_=1, subjects=None, extra=()):
        out = [str(self.env.run_path), "--prompt", str(PROMPT), "--round", str(round_),
               "--question", str(self.question)]
        for s in subjects or [self.env.subject]:
            out += ["--subject", str(s)]
        return out + list(extra)

    def run_main(self, argv=None, gateway=None):
        gateway = gateway or trc.Gateway()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.rc.main(argv if argv is not None else self.argv(), transport=gateway.transport)
        # Booleans with fixed messages: a failure never prints the text or the bytes it read.
        for name, text in (("stdout", out.getvalue()), ("stderr", err.getvalue())):
            self.assertFalse(DUMMY_KEY in text, f"the key reached {name}")
        for p in self.env.run_dir.rglob("*"):
            if p.is_file() and not p.is_symlink():
                self.assertFalse(DUMMY_KEY.encode() in p.read_bytes(), f"the key reached {p.name}")
        return rc, out.getvalue(), err.getvalue(), gateway

    # -- git and files ------------------------------------------------------------------------

    def git_out(self, *args) -> bytes:
        proc = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, env=sup.scrubbed_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def head(self) -> str:
        return self.git_out("rev-parse", "HEAD").decode().strip()

    def head_blob(self, rel: str) -> bytes | None:
        proc = subprocess.run(["git", "-C", str(self.root), "cat-file", "blob", f"HEAD:{rel}"],
                              capture_output=True, env=sup.scrubbed_env())
        return proc.stdout if proc.returncode == 0 else None

    def commits_since(self, base: str) -> list[tuple[str, list[str]]]:
        out = []
        for sha in self.git_out("rev-list", "--reverse", f"{base}..HEAD").decode().split():
            subject = self.git_out("log", "-1", "--format=%s", sha).decode().strip()
            names = self.git_out("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha)
            out.append((subject, sorted(n.decode() for n in names.split(b"\0") if n)))
        return out

    def attempt_rel(self, round_=1, ordinal=1) -> str:
        return self.env.rel(self.env.council) + "/" + cr.attempt_file_name(sup.RUN_ID, TAG, PROMPT, round_, ordinal)

    def attempts(self) -> list[str]:
        return sorted(self.env.rel(p) for p in self.env.council.glob("*.attempt.json"))

    def records(self) -> list[str]:
        return sorted(self.env.rel(p) for p in self.env.council.glob("*.json")
                      if not p.name.endswith(".attempt.json"))

    def pending(self) -> list[Path]:
        d = cr.pending_dir(self.root, sup.BOOK_ID, sup.RUN_ID)
        return sorted(d.iterdir()) if d is not None and d.is_dir() else []

    def diagnostics(self) -> list[dict]:
        path = self.env.run_dir / "council-preflight.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def install_hook(self, body: str) -> None:
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/bin/sh\n" + body + "\nexit 0\n")
        hook.chmod(0o755)

    def ran(self, round_=1) -> dict:
        """The positive control: the run claims, deliberates (three requests) and commits."""
        rc, out, err, gw = self.run_main(self.argv(round_))
        self.assertEqual((rc, err, len(gw.requests)), (0, "", 3), out)
        return json.loads(out)


#: A pre-commit hook body that refuses any commit staging an attempt record.
REJECT_ATTEMPT = 'git diff --cached --name-only | grep -q "\\.attempt\\.json$" && { echo "hook: no" >&2; exit 1; }'
#: A pre-commit hook body that refuses any commit staging a council record.
REJECT_RECORD = ('git diff --cached --name-only | grep "/council/[^/]*\\.json$" | grep -qv "\\.attempt\\.json$" '
                 '&& { echo "hook: no" >&2; exit 1; }')


class ClaimTests(_Harness):

    def test_the_runner_commits_a_sealed_attempt_before_its_requests_and_the_record_names_it(self):
        """The two runner commits, in order, each holding exactly its own path; the attempt binds
        the round's inputs; the record names it by path, committed sha256 and id, and reuses its
        registry block."""
        before = self.head()
        seen_at_first_request: list = []
        gw = trc.Gateway()
        inner = gw._handle

        def handle(request):
            if not seen_at_first_request:
                seen_at_first_request.append(self.head_blob(self.attempt_rel()))
            return inner(request)

        import httpx
        gw.transport = httpx.MockTransport(handle)
        rc, out, err, gw = self.run_main(gateway=gw)
        self.assertEqual((rc, err, len(gw.requests)), (0, "", 3), out)
        summary = json.loads(out)
        att = self.attempt_rel()
        self.assertEqual(summary["attempt"], att)
        data = (self.root / att).read_bytes()
        self.assertEqual(seen_at_first_request, [data], "the attempt was not in HEAD at the first request")
        self.assertTrue(cr.seal_holds(data))
        attempt = json.loads(data)
        self.assertEqual(cr.schema_errors(attempt, "council-attempt"), [])
        self.assertEqual((attempt["book"]["id"], attempt["run_id"], attempt["binding"], attempt["round"],
                          attempt["ordinal"], attempt["council_kind"]),
                         (sup.BOOK_ID, sup.RUN_ID, {"prompt": PROMPT, "module_tag": TAG}, 1, 1, "adr"))
        self.assertEqual(attempt["subjects"], [{"path": sup.SUBJECT, "sha256": self.env.sha(sup.SUBJECT)}])
        record = json.loads((self.root / summary["record"]).read_bytes())
        self.assertEqual(record["attempt"], {"path": att, "sha256": cr.sha256_bytes(data),
                                             "attempt_id": attempt["attempt_id"]})
        self.assertEqual(record["registry"], attempt["registry"])
        self.assertEqual((record["format_version"], record["question"]), ("2", attempt["question"]))
        self.assertTrue(cr.seal_holds((self.root / summary["record"]).read_bytes()))
        self.assertEqual(self.commits_since(before), [
            (f"crux council attempt: {sup.BOOK_ID} {sup.RUN_ID} prompt 3 round 1 attempt 1", [att]),
            (f"crux council record: {sup.BOOK_ID} {sup.RUN_ID} prompt 3 round 1 attempt 1", [summary["record"]]),
            (f"crux council diagnostics: {sup.BOOK_ID} {sup.RUN_ID} prompt 3",
             [self.env.rel(self.env.run_dir) + "/council-preflight.jsonl"]),
        ])
        self.assertEqual(self.pending(), [], "the pending copy outlived the verified commit")
        self.assertEqual([(d["event"], d["prompt"]) for d in self.diagnostics()], [("claim", PROMPT)])

    def test_the_result_path_runs_in_order(self):
        """pending copy -> working-tree record -> commit_owned -> remove_pending -> summary."""
        rc_mod = self.rc
        order: list[str] = []
        real = {"write_pending": rc_mod.council_commit.write_pending,
                "commit_owned": rc_mod.council_commit.commit_owned,
                "remove_pending": rc_mod.council_commit.remove_pending,
                "_write_new": rc_mod._write_new, "_print_summary": rc_mod._print_summary}

        def spy(name, owner):
            def call(*a, **kw):
                tag = name
                if name == "commit_owned":
                    owned = list(a[1])
                    tag = "commit_owned:" + ("attempt" if any(k.endswith(".attempt.json") for k in owned)
                                             else "diagnostics" if any(k.endswith("council-preflight.jsonl")
                                                                       for k in owned) else "record")
                if name == "_write_new":
                    tag = "_write_new:" + a[2].rsplit(".", 1)[-1]
                order.append(tag)
                return real[name](*a, **kw)
            return mock.patch.object(owner, name, side_effect=call)

        with spy("write_pending", rc_mod.council_commit), spy("commit_owned", rc_mod.council_commit), \
                spy("remove_pending", rc_mod.council_commit), spy("_write_new", rc_mod), \
                spy("_print_summary", rc_mod):
            rc, out, err, gw = self.run_main(self.argv(extra=["--retain-subjects"]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(order, ["commit_owned:attempt", "write_pending", "_write_new:retained",
                                 "_write_new:json", "commit_owned:record", "remove_pending", "_print_summary",
                                 "commit_owned:diagnostics"])

    def test_the_attempt_lock_is_held_while_the_council_deliberates_and_released_after(self):
        import fcntl
        import httpx
        probes: list[str] = []
        gw = trc.Gateway()
        inner = gw._handle
        path = self.root / self.attempt_rel()

        def probe() -> str:
            fd = os.open(path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return "free"
            except BlockingIOError:
                return "held"
            finally:
                os.close(fd)

        def handle(request):
            if not probes:
                probes.append(probe())
            return inner(request)

        gw.transport = httpx.MockTransport(handle)
        rc, out, err, gw = self.run_main(gateway=gw)
        self.assertEqual(rc, 0, err)
        self.assertEqual(probes, ["held"], "a live runner's attempt lock was free")
        self.assertEqual(probe(), "free", "the lock outlived the runner")  # positive control on the probe

    def test_the_ordinal_increments_after_a_resolved_attempt(self):
        """A no-key record resolves attempt 1 and takes no place; the next round 1 is attempt 2."""
        with mock.patch.dict(os.environ):
            del os.environ["OPENROUTER_API_KEY"]
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, gw.requests), (1, []), err)
        self.assertEqual(json.loads(out)["attempt"], self.attempt_rel(1, 1))
        summary = self.ran(1)
        self.assertEqual(summary["attempt"], self.attempt_rel(1, 2))
        self.assertEqual(self.attempts(), [self.attempt_rel(1, 1), self.attempt_rel(1, 2)])
        record = json.loads((self.root / summary["record"]).read_text())
        self.assertEqual(record["attempt"]["path"], self.attempt_rel(1, 2))


class AttemptOpenTests(_Harness):

    def test_a_committed_open_attempt_refuses_a_second_invocation_before_preflight(self):
        """Deliberation raises after the claim: the attempt is committed and open. A rerun, even
        with a mistyped subject, is refused `attempt-open` with no request, no write and no
        diagnostics refusal line; the attempt-open check precedes preflight."""
        with mock.patch.object(self.rc, "_ran_record", side_effect=RuntimeError("after the requests")):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, len(gw.requests)), (2, 3), err)
        att = self.attempt_rel()
        self.assertEqual(self.head_blob(att), (self.root / att).read_bytes())
        self.assertIn("--recover", err)
        self.assertIn(att, err)
        head, files = self.head(), sorted(p.name for p in self.env.council.iterdir())
        for subjects in (None, [self.root / TYPO]):
            with self.subTest(subjects=subjects):
                rc, out, err, gw = self.run_main(self.argv(subjects=subjects))
                body = json.loads(out)
                self.assertEqual((rc, body["refused"], body["record"], body["attempt"], gw.requests),
                                 (1, "attempt-open", None, att, []))
                self.assertEqual((self.head(), sorted(p.name for p in self.env.council.iterdir())), (head, files))
        self.assertEqual([d for d in self.diagnostics() if d["event"] == "refusal"], [])

    def test_a_committed_attempt_deleted_from_the_working_tree_still_refuses_a_new_claim(self):
        """A committed open attempt whose file is deleted, the deletion not staged, still holds the
        scope: the rerun is refused `attempt-open` naming it, with no request, and HEAD keeps its
        bytes. Red when the runner reads only working-tree attempts: it claims the same name again,
        commits over the attempt in HEAD and convenes a replacement round (three requests)."""
        with mock.patch.object(self.rc, "_ran_record", side_effect=RuntimeError("after the requests")):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, len(gw.requests)), (2, 3), err)
        att = self.attempt_rel()
        committed = self.head_blob(att)
        self.assertIsNotNone(committed)
        # Positive control: with the file in place, the rerun is refused on the attempt.
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, json.loads(out)["refused"], gw.requests), (1, "attempt-open", []), err)
        (self.root / att).unlink()
        head = self.head()
        rc, out, err, gw = self.run_main()
        body = json.loads(out)
        self.assertEqual((rc, body.get("refused"), body.get("record"), body.get("attempt"), len(gw.requests)),
                         (1, "attempt-open", None, att, 0), err)
        self.assertEqual((self.head(), self.head_blob(att)), (head, committed))
        self.assertFalse((self.root / att).exists())

    def test_a_void_with_a_surviving_result_does_not_free_the_scope(self):
        """The owner commits a void-attempt while the attempt's own council record and pending copy
        survive; the gate refuses that void, so the runner treats the attempt as open: refused
        `attempt-open` naming it, with no request. Red when the runner reads any committed void as
        closing the attempt: it claims attempt 2 and pays for a replacement round."""
        self.install_hook(REJECT_RECORD)
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, len(gw.requests)), (2, 3), err)
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        att = self.attempt_rel()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(len(self.records()), 1)
        void = self.env.write("void.json", self.env.void_doc(self.root / att, module_tag=TAG), commit=True)
        self.assertIsNotNone(self.head_blob(self.env.rel(void)))
        head = self.head()
        rc, out, err, gw = self.run_main()
        body = json.loads(out)
        self.assertEqual((rc, body.get("refused"), body.get("attempt"), len(gw.requests)),
                         (1, "attempt-open", att, 0), err)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.attempts(), [att])

    def test_an_uncommitted_claim_file_refuses_a_second_invocation(self):
        doc = self.env.attempt_doc(prompt=PROMPT)
        path = self.env.write_sealed(cr.attempt_file_name(sup.RUN_ID, TAG, PROMPT, 1, 1), doc, commit=False)
        before = path.read_bytes()
        rc, out, err, gw = self.run_main()
        body = json.loads(out)
        self.assertEqual((rc, body["refused"], body["attempt"], gw.requests),
                         (1, "attempt-open", self.env.rel(path), []))
        self.assertEqual(path.read_bytes(), before)
        # Positive control: once the claim file is gone, the same invocation claims and runs.
        path.unlink()
        self.ran(1)

    def test_an_existing_attempt_name_at_the_claim_is_an_attempt_open_refusal_and_is_never_overwritten(self):
        """The exclusive create: another invocation's claim appears after every pre-claim check
        has passed, at the claim step itself, so only the link's refusal to replace a name stops it.

        The rival is written when the runner takes the lock on its temporary file, the step just
        before the link. Keyed on that step rather than on a call count, it lands after the
        open-attempt check and after preflight, so the pre-claim refusal cannot be what refuses.
        Mutation: link with `os.replace` instead of `os.link` -> the rival is overwritten, the
        runner claims and makes three requests."""
        import fcntl as real_fcntl
        doc = self.env.attempt_doc(prompt=PROMPT)
        name = cr.attempt_file_name(sup.RUN_ID, TAG, PROMPT, 1, 1)
        rival = self.env.council / name
        real_flock = real_fcntl.flock
        injected: list[str] = []

        def flock(fd, op):
            if not injected and op == real_fcntl.LOCK_EX | real_fcntl.LOCK_NB:
                # Control: nothing holds the name when the runner reaches its claim step.
                injected.append("absent" if not rival.exists() else "present")
                self.env.write_sealed(name, doc, commit=False)
            return real_flock(fd, op)

        fake = types.SimpleNamespace(flock=flock, LOCK_EX=real_fcntl.LOCK_EX, LOCK_NB=real_fcntl.LOCK_NB,
                                     LOCK_UN=real_fcntl.LOCK_UN)
        with mock.patch.object(self.rc, "fcntl", fake):
            rc, out, err, gw = self.run_main()
        self.assertEqual(injected, ["absent"], "control: the rival was not written at the claim step")
        body = json.loads(out)
        self.assertEqual((rc, body.get("refused"), body.get("attempt"), body.get("record"), len(gw.requests)),
                         (1, "attempt-open", self.attempt_rel(), None, 0), "the rival claim did not refuse")
        self.assertIn("another invocation claimed this round", body.get("reason", ""))
        self.assertEqual(json.loads(rival.read_bytes())["attempt_id"], doc["attempt_id"],
                         "the rival claim was overwritten")
        self.assertEqual([p.name for p in self.env.council.iterdir() if p.name.endswith(".tmp")], [])
        self.assertEqual(self.diagnostics(), [], "a refused claim wrote a diagnostics line")


class ClaimFailureTests(_Harness):

    def test_a_failed_attempt_commit_makes_no_request_and_exits_2_naming_recovery(self):
        self.install_hook(REJECT_ATTEMPT)
        before = self.head()
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertEqual(self.head(), before)
        self.assertIn("hook-or-commit-failed", err)
        self.assertIn(self.attempt_rel(), err)
        self.assertIn("run the process check, then run-council.py --recover", err)
        self.assertEqual(self.records(), [])
        self.assertIsNone(self.head_blob(self.attempt_rel()))
        # The uncommitted claim stays and holds the scope until recovery releases it.
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, json.loads(out)["refused"], gw.requests), (1, "attempt-open", []))
        # Positive control: with the hook gone and the claim released, the same invocation claims,
        # commits and makes its three requests.
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        (self.root / self.attempt_rel()).unlink()
        self.ran(1)

    def test_a_failed_post_claim_recheck_exits_2_with_the_attempt_open_and_no_request(self):
        real = self.rc.council_gate.expected_round
        calls = {"n": 0}

        def expected(*a, **kw):
            calls["n"] += 1
            return real(*a, **kw) if calls["n"] == 1 else 2  # the round moved after the claim

        with mock.patch.object(self.rc.council_gate, "expected_round", side_effect=expected):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertEqual(calls["n"], 2, "the expected round was not re-checked after the claim")
        att = self.attempt_rel()
        self.assertEqual(self.head_blob(att), (self.root / att).read_bytes(), "the attempt is not committed")
        self.assertIn("claim no longer holds", err)
        self.assertIn("--recover", err)
        self.assertEqual(self.records(), [])

    def test_the_recheck_refuses_another_open_attempt_in_scope(self):
        real = self.rc.council_gate.attempt_states
        calls = {"n": 0}
        rival = self.env.attempt_doc(prompt=PROMPT, round=1, ordinal=7)

        def states(*a, **kw):
            # Once this runner's own attempt is committed (the post-claim re-check), a rival attempt
            # is in scope. Keyed on the claim, not on a call count: expected_round reads attempt
            # states too, so the count of calls before the claim is not fixed.
            if not calls["n"] and self.head_blob(self.attempt_rel()) is not None:
                calls["n"] += 1
                self.env.write_sealed(cr.attempt_file_name(sup.RUN_ID, TAG, PROMPT, 1, 7), rival, commit=False)
            return real(*a, **kw)

        with mock.patch.object(self.rc.council_gate, "attempt_states", side_effect=states):
            rc, out, err, gw = self.run_main()
        self.assertEqual(calls["n"], 1, "control: the rival was injected after the claim")
        self.assertEqual((rc, gw.requests), (2, []), err)
        self.assertIn("another attempt in scope is open", err)

    def test_a_claim_that_resolves_outside_the_run_directory_exits_2_naming_the_attempt_and_recovery(self):
        """The claim file exists once the link lands, so a containment failure after it is a
        post-claim exit 2: stderr names the attempt and the recovery step, nothing is committed
        and no request is made.
        Mutation: run the containment check before the attempt is bound to the run -> stderr names
        no attempt and no recovery step while the claim file stays."""
        real_islink = os.path.islink
        seen: list[str] = []

        def islink(p):
            if str(p).endswith(".attempt.json"):
                seen.append(os.path.basename(str(p)))
                return True
            return real_islink(p)

        before = self.head()
        with mock.patch.object(os.path, "islink", side_effect=islink):
            rc, out, err, gw = self.run_main()
        att = self.attempt_rel()
        self.assertTrue(seen, "control: the containment check never read the claim")
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertIn("resolved outside the run directory", err)
        self.assertIn(att, err)
        self.assertIn("run the process check, then run-council.py --recover", err)
        self.assertTrue((self.root / att).is_file(), "control: the claim file is on disk")
        self.assertEqual(self.head(), before, "a claim that failed containment was committed")

    def test_no_fcntl_refuses_before_the_claim(self):
        before = self.head()
        with mock.patch.object(self.rc, "fcntl", None):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertIn("fcntl", err)
        self.assertEqual((self.head(), self.attempts(), self.records()), (before, [], []))
        self.ran(1)  # positive control: with fcntl the same invocation claims and runs

    def test_an_unreadable_registry_refuses_before_the_claim(self):
        from crux.core import llm_caller
        with mock.patch.object(llm_caller, "CONFIG_PATH", self.tmp / "absent-registry.json"):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertIn("router registry cannot be read", err)
        self.assertEqual(self.attempts(), [])

    def test_a_failed_record_commit_exits_2_naming_the_record_the_attempt_and_recovery(self):
        self.install_hook(REJECT_RECORD)
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, len(gw.requests)), (2, "", 3), err)
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertIsNone(self.head_blob(recs[0]))
        self.assertIn(recs[0], err)
        self.assertIn(self.attempt_rel(), err)
        self.assertIn("--recover", err)
        self.assertEqual([p.read_bytes() for p in self.pending()], [(self.root / recs[0]).read_bytes()])

    def test_a_deliberation_exception_exits_2_with_the_attempt_open(self):
        from crux.council import async_council as ac
        with mock.patch.object(ac.AsyncCouncil, "deliberate", side_effect=RuntimeError("gateway fault")):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("council failed before it returned", err)
        self.assertIn(self.attempt_rel(), err)
        self.assertIn("--recover", err)
        self.assertEqual(self.head_blob(self.attempt_rel()), (self.root / self.attempt_rel()).read_bytes())
        self.assertEqual(self.records(), [])


class RefusedCommitHintTests(_Harness):
    """What exit 2 tells the conductor when `council_commit` reports outside work moved or an
    owned path left staged (`CommitRefused.moved` and `.staged`)."""

    def refuse(self, suffix: str, **attrs):
        """Shim `commit_owned` to raise a refusal for the first commit whose owned paths all end in `suffix`."""
        real = self.rc.council_commit.commit_owned
        err_cls = self.rc.council_commit.CommitRefused

        def commit_owned(repo, owned, message, **kw):
            if all(k.endswith(suffix) for k in owned):
                raise err_cls("timeout", "the commit passed its bound", **attrs)
            return real(repo, owned, message, **kw)

        return mock.patch.object(self.rc.council_commit, "commit_owned", side_effect=commit_owned)

    def test_moved_paths_are_named_and_the_remedy_keeps_its_order(self):
        """Red without the moved reading: stderr names no moved path and no stash step."""
        with self.refuse(".attempt.json", moved=("src/a.txt", "refs/stash")):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        for needle in ("src/a.txt", "refs/stash", "git stash list", "cache directory", "index.lock",
                       "run-council.py --recover"):
            self.assertIn(needle, err)
        self.assertLess(err.index("git stash list"), err.index("index.lock"))
        self.assertLess(err.index("index.lock"), err.index("run-council.py --recover"))
        self.assertLess(err.index("src/a.txt"), err.index("git stash list"))
        # In a linked worktree the lock sits in the git directory, not under `.git/`.
        self.assertNotIn(".git/index.lock", err)
        self.assertIn("index.lock in the git directory", err)

    def test_a_moved_path_that_matches_the_secret_scan_is_withheld_and_the_steps_survive(self):
        with self.refuse(".attempt.json", moved=(DUMMY_KEY + ".txt", "src/b.txt")):
            rc, out, err, gw = self.run_main()
        self.assertEqual(rc, 2, err)
        self.assertIn("src/b.txt", err)
        self.assertIn("git stash list", err)
        self.assertIn("run-council.py --recover", err)

    def test_an_outside_state_that_was_not_compared_is_named_as_such(self):
        """`council_commit.MOVED_UNKNOWN` is a marker, not a path. Red when it is printed as a moved
        path: stderr claims a path named `<outside state not compared>` moved."""
        unknown = self.rc.council_commit.MOVED_UNKNOWN
        with self.refuse(".attempt.json", moved=(unknown,)):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, gw.requests), (2, []), err)
        self.assertNotIn(unknown, err)
        for needle in ("could not be compared", "git status", "git stash list", "run-council.py --recover"):
            self.assertIn(needle, err)
        self.assertLess(err.index("git stash list"), err.index("run-council.py --recover"))

    def test_nothing_moved_names_no_stash_step(self):
        """Control: a timeout that moved nothing keeps the old hint, with no moved-path sentence."""
        with self.refuse(".attempt.json"):
            rc, out, err, gw = self.run_main()
        self.assertEqual(rc, 2, err)
        self.assertIn("run-council.py --recover", err)
        self.assertNotIn("git stash list", err)
        self.assertNotIn("stays staged", err)

    def test_a_staged_record_is_named_and_must_not_be_committed_by_hand(self):
        rec_suffix = ".json"
        real = self.rc.council_commit.commit_owned
        err_cls = self.rc.council_commit.CommitRefused
        staged: list[str] = []

        def commit_owned(repo, owned, message, **kw):
            if "crux council record" in message:
                staged.extend(owned)
                raise err_cls("timeout", "the commit passed its bound", staged=tuple(owned))
            return real(repo, owned, message, **kw)

        with mock.patch.object(self.rc.council_commit, "commit_owned", side_effect=commit_owned):
            rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, len(gw.requests)), (2, "", 3), err)
        self.assertEqual(len(staged), 1)
        self.assertTrue(staged[0].endswith(rec_suffix) and not staged[0].endswith(".attempt.json"))
        self.assertIn(f"The record stays staged ({staged[0]}) and must not be committed by hand", err)
        self.assertIn("run-council.py --recover", err)

    def test_a_staged_attempt_says_attempt(self):
        """Recovery releases an uncommitted claim; it never commits it. Red when the hint says
        recovery commits the staged attempt."""
        with self.refuse(".attempt.json", staged=("docs/x.attempt.json",)):
            rc, out, err, gw = self.run_main()
        self.assertEqual(rc, 2, err)
        self.assertIn("The attempt stays staged (docs/x.attempt.json) and must not be committed by hand", err)
        self.assertIn("recovery releases the uncommitted claim", err)
        self.assertNotIn("recovery commits it", err)

    def test_a_failing_hook_that_set_work_aside_is_named_once_with_the_remedy_before_recovery(self):
        """A real `set -e` pre-commit hook sets an unstaged edit aside, fails a lint step and exits
        before its restore, at the attempt commit; nothing is shimmed. Red before the outside-state
        comparison ran on a failed commit: stderr names no moved path and no stash step. Red while
        the refusal's detail and the hint both carry the remedy: `git stash list` appears twice.
        Positive control: the edit did move."""
        work = self.root / "notes.txt"
        work.write_text("base\n")
        sup.commit_all(self.root, "notes")
        work.write_text("the user's unstaged edit\n")
        backup = self.tmp / "hook-backup"
        self.install_hook(f'set -e\ncp notes.txt "{backup}"\nprintf "base\\n" > notes.txt\nfalse\n'
                          f'cp "{backup}" notes.txt')
        rc, out, err, gw = self.run_main()
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        self.assertEqual(work.read_text(), "base\n")  # the control: the hook left the edit set aside
        self.assertIn("hook-or-commit-failed", err)
        self.assertIn("notes.txt", err)
        self.assertEqual(err.count("git stash list"), 1, err)
        self.assertLess(err.index("notes.txt"), err.index("git stash list"))
        self.assertLess(err.index("git stash list"), err.index("index.lock"))
        self.assertLess(err.index("index.lock"), err.index("run-council.py --recover"))

    def test_the_recovery_command_quotes_a_run_path_with_a_space(self):
        """The run snapshot is named with a space and passed with `--book`. Red without quoting:
        the printed command splits the path in two words."""
        spaced = self.env.run_dir / "run RUN-001 copy.yaml"
        spaced.write_bytes(self.env.run_path.read_bytes())
        sup.commit_all(self.root, "spaced snapshot")
        self.install_hook(REJECT_ATTEMPT)
        argv = self.argv()
        argv[0] = str(spaced)
        rc, out, err, gw = self.run_main(argv + ["--book", str(self.env.book_path)])
        self.assertEqual((rc, out, gw.requests), (2, "", []), err)
        quoted = shlex.quote(self.env.rel(spaced))
        self.assertTrue(quoted.startswith("'"), "control: the path needs quoting")
        self.assertIn(f"run-council.py --recover {quoted} --prompt {PROMPT}", err)


class RecoverDispatchTests(_Harness):

    def test_recover_hands_the_remaining_arguments_to_the_recovery_module_before_any_key_read(self):
        seen: list = []
        stub = types.ModuleType("council_recovery")
        stub.main = lambda argv: seen.append(list(argv)) or 1
        with mock.patch.dict(sys.modules, {"council_recovery": stub}), \
                mock.patch.object(self.rc, "_read_key", side_effect=AssertionError("the key was read")), \
                mock.patch.object(self.rc, "_parser", side_effect=AssertionError("the parser was built")):
            rc, out, err, gw = self.run_main(["--recover", "run-RUN-001.yaml", "--prompt", "3", "--probe"])
        self.assertEqual((rc, seen, gw.requests), (1, [["run-RUN-001.yaml", "--prompt", "3", "--probe"]], []))

    def test_recover_with_no_recovery_module_exits_2_with_a_fixed_message(self):
        with mock.patch.dict(sys.modules, {"council_recovery": None}), \
                mock.patch.object(self.rc, "_read_key", side_effect=AssertionError("the key was read")):
            rc, out, err, gw = self.run_main(["--recover", "run-RUN-001.yaml"])
        self.assertEqual((rc, out), (2, ""))
        self.assertEqual(err, "run-council: recovery is unavailable: council_recovery.py cannot be imported\n")

    def test_only_a_leading_recover_dispatches(self):
        """`--recover` elsewhere is an unknown argument for the convening parser (exit 2)."""
        stub = types.ModuleType("council_recovery")
        stub.main = mock.Mock(return_value=0)
        with mock.patch.dict(sys.modules, {"council_recovery": stub}):
            rc, out, err, gw = self.run_main(self.argv(extra=["--recover"]))
        self.assertEqual(rc, 2)
        stub.main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
