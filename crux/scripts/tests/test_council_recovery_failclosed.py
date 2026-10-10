"""`council_recovery`: the fail-closed rows, the commit-refusal report, the usage errors and the
no-gateway claim through `run-council.py --recover`.

Every row here is a state in which recovery must refuse, name the state and change nothing. Each
test builds a real git repository (`_council_gate_support.Env`), drives `council_recovery.main`, and
asserts three things: the result token with its exit code, that HEAD, the index, the council
directory's files and the pending copies are unchanged, and that no gateway request was made. A
spy that counts every socket connection and every httpx send backs the last claim, and its positive
control shows it counts.

Each test names the mutation that turns it red: the row falls through to the committing or
recognising branch, and the frozen state or the token differs.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
import _council_gate_support as sup  # noqa: E402
from test_council_recovery import KEY_SHAPED, PROMPT, _Base, json_docs  # noqa: E402

import council_commit  # noqa: E402
import council_records as cr  # noqa: E402
import council_recovery as rec  # noqa: E402

SHIM = HERE / "_council_cli_shim.py"


@contextlib.contextmanager
def gateway_spy():
    """Count every socket connection and every httpx send made while the block runs."""
    import httpx
    calls: list[str] = []

    def note(label):
        def spy(*a, **k):
            calls.append(label)
            raise AssertionError(f"{label}: a request was attempted")
        return spy

    with mock.patch.object(socket.socket, "connect", note("socket")), \
            mock.patch.object(httpx.Client, "send", note("httpx")), \
            mock.patch.object(httpx.AsyncClient, "send", note("httpx-async")):
        yield calls


class _Closed(_Base):
    """A refusal helper: the state before, the call under a gateway spy, the state after."""

    def refuse(self, token: str, *extra: str, code: int = 1, prompt: int | None = PROMPT, expect=None):
        before = self.frozen()
        with gateway_spy() as calls:
            rc, docs, err = self.recover(*extra, prompt=prompt)
        self.assertEqual(calls, [], "recovery made a gateway request")
        self.assertEqual((self.report(docs)["recovery"], rc), (token, code), err)
        self.assertEqual(self.frozen(), before, "a refusal changed the repository or the pending copies")
        if expect:
            self.assertIn(expect, err)
        return docs, err

    def sealed_doc(self, att: Path, mutate) -> bytes:
        return self.record_bytes(att, mutate=mutate)


class SpyControlTests(unittest.TestCase):

    def test_the_gateway_spy_counts_a_socket_connection_and_an_httpx_send(self):
        """Positive control for every `calls == []` assertion in this module."""
        import httpx
        with gateway_spy() as calls:
            sock = socket.socket()
            self.addCleanup(sock.close)
            with self.assertRaises(AssertionError):
                sock.connect(("127.0.0.1", 9))
            with self.assertRaises(AssertionError):
                httpx.Client().send(httpx.Request("GET", "http://127.0.0.1:9/"))
        self.assertEqual(calls, ["socket", "httpx"])


class PendingCopyRowTests(_Closed):

    def test_a_sealed_pending_copy_failing_the_schema_is_a_mismatch_not_committed(self):
        """Row `_decide_pending` schema check. The copy is sealed and names its attempt, so only the
        schema stops it. Positive control: the unmutated copy commits. Mutation: skip the check ->
        the copy reaches resolution and, once that passes too, the committing branch."""
        att = self.attempt()
        bad = self.sealed_doc(att, lambda d: d.__setitem__("outcome", "not-an-outcome"))
        self.assertTrue(cr.seal_holds(bad))
        self.assertTrue(cr.schema_errors(json.loads(bad), "council-record"))
        self.pend(self.rec_name(), bad)
        self.refuse("mismatch", expect="fails the council-record schema")
        self.refuse("mismatch", "--owner-commit-pending", expect="fails the council-record schema")
        council_commit.remove_pending(self.root, sup.BOOK_ID, sup.RUN_ID, self.rec_name())
        self.pend(self.rec_name(), self.record_bytes(att))
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)

    def test_a_schema_valid_pending_copy_that_does_not_resolve_its_attempt_is_a_mismatch(self):
        """Row `resolution_problem`. The copy names the attempt's path with another sha256. Positive
        control: the discriminator below holds (seal and schema are fine) and the unmutated copy
        commits. Mutation: skip the check -> committed."""
        att = self.attempt()
        bad = self.sealed_doc(att, lambda d: d["attempt"].__setitem__("sha256", "0" * 64))
        self.assertTrue(cr.seal_holds(bad))
        self.assertEqual(cr.schema_errors(json.loads(bad), "council-record"), [])
        self.pend(self.rec_name(), bad)
        self.refuse("mismatch", expect="does not resolve the attempt")
        self.refuse("mismatch", "--owner-commit-pending", expect="does not resolve the attempt")

    def test_a_pending_copy_that_is_not_json_is_a_mismatch(self):
        """Row `_decide` unparseable copy. Nothing else is open, so only this row holds the answer.
        Mutation: skip the item -> `nothing-open`, exit 0."""
        self.pend(self.rec_name(), b"this is not json {")
        self.refuse("mismatch", expect="not parseable JSON")

    def test_a_pending_copy_in_head_with_a_different_working_tree_record_is_a_mismatch(self):
        """Row `head == data` with `wt != data`. HEAD holds the pending bytes, the working tree
        holds a reformat. Positive control: with the working tree equal, the same state is
        recognised. Mutation: recognise on HEAD alone -> `recognised`, pending removed."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), data, commit=True)
        path.write_bytes((json.dumps(json.loads(data), indent=4) + "\n").encode())
        self.refuse("mismatch", expect="differs from HEAD and the pending copy")
        path.write_bytes(data)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)

    def test_a_record_equal_to_its_pending_copy_in_head_with_an_unproven_attempt_is_a_mismatch(self):
        """Row `att.committed` false. The attempt file matches HEAD and the working tree but the
        index holds another version, so the attempt is not committed. Positive control: with the
        index restored the same state is recognised. Mutation: drop the check -> `recognised`."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), data, commit=True)
        original = att.read_bytes()
        att.write_bytes(original + b"\n")
        sup.git(self.root, "add", "--", self.env.rel(att))
        att.write_bytes(original)
        self.assertFalse(self.state(att).committed)
        self.refuse("mismatch", expect="attempt record itself is not committed")
        sup.git(self.root, "add", "--", self.env.rel(att))
        self.assertTrue(self.state(att).committed)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)


class AttemptRowTests(_Closed):

    def test_an_uncommitted_attempt_named_by_a_working_tree_record_is_unproven_not_released(self):
        """Row `names or st.naming` for an uncommitted attempt. Positive control: without the
        record the same attempt is released (ReleaseTests). Mutation: release it -> the attempt
        file is deleted, the frozen state differs."""
        att = self.attempt(commit=False)
        self.put(self.rec_name(), self.record_bytes(att))
        self.refuse("unproven", expect="uncommitted attempt is named by a council record")
        self.assertTrue(att.exists())

    def test_an_uncommitted_attempt_named_by_a_pending_copy_is_unproven_not_released(self):
        att = self.attempt(commit=False)
        self.pend(self.rec_name(), self.record_bytes(att))
        self.refuse("unproven", expect="uncommitted attempt is named by a council record")
        self.assertTrue(att.exists())

    def test_a_committed_record_that_does_not_resolve_its_attempt_is_a_mismatch(self):
        """Row `_decide` committed record, no pending copy. Mutation: report nothing-open or
        recognise -> the gate-visible attempt stays open under a clean report."""
        att = self.attempt()
        bad = self.sealed_doc(att, lambda d: d["attempt"].__setitem__("sha256", "0" * 64))
        self.put(self.rec_name(), bad, commit=True)
        self.assertTrue(self.state(att).open)
        self.refuse("mismatch", expect="does not resolve the attempt")

    def test_a_committed_attempt_missing_from_the_working_tree_is_unproven(self):
        """Row `head_names` loop. Discovery cannot see the deleted attempt, so only this loop does.
        Positive control: an attempt of another module in the same state is out of scope and
        recovery reports nothing-open. Mutation: skip the loop -> nothing-open."""
        att = self.attempt()
        os.unlink(att)
        docs, _ = self.refuse("unproven", expect="missing from the working tree")
        self.assertEqual(self.report(docs)["attempt"], self.env.rel(att))

    def test_a_committed_attempt_of_another_scope_missing_from_the_working_tree_is_out_of_scope(self):
        other = self.attempt(module_tag="verify-1", prompt=6)
        os.unlink(other)
        self.refuse("nothing-open", code=0)

    def test_a_pending_copy_naming_an_attempt_that_is_not_in_the_working_tree_is_unproven(self):
        """Row `_decide` last loop. The attempt was never committed and its file is gone. Mutation:
        commit the copy -> HEAD moves."""
        att = self.attempt(commit=False)
        data = self.record_bytes(att)
        os.unlink(att)
        self.pend(self.rec_name(), data)
        docs, _ = self.refuse("unproven", expect="names an attempt that is not in the working tree")
        self.assertEqual(self.report(docs)["record"], self.env.rel(self.env.council / self.rec_name()))


class ClaimLineRowTests(_Closed):

    def log(self, *lines: str) -> Path:
        path = self.env.run_dir / "council-preflight.jsonl"
        path.write_text("".join(line + "\n" for line in lines))
        return path

    def claim(self, **kw) -> str:
        doc = {"at": sup.ts(1, 0), "event": "claim", "prompt": PROMPT, "round": 1, "codes": [], "fields": []}
        doc.update(kw)
        return json.dumps(doc)

    def setUp(self):
        super().setUp()
        self.att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(self.att), commit=True)

    def test_a_log_with_no_claim_for_this_prompt_still_recognises_the_record(self):
        """Row `last is None`. Another prompt's claim and a line that is not JSON are no trace of a
        later round. Mutation: treat an absent claim as a later one -> nothing-open, and the
        conductor never learns the outcome."""
        self.log(self.claim(prompt=3, round=4), "not json")
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)

    def test_a_claim_line_recovery_cannot_order_is_not_proof_that_none_came_later(self):
        """Row `claim_round` / `at` / `stamp` unorderable. Positive control: an orderable claim
        written before the record leaves the record recognised. Mutation: treat an unorderable
        claim as not later -> `recognised`, a possibly earlier round reprinted as current."""
        self.log(self.claim())
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)
        for label, line in (("a non-integer round", self.claim(round="two")),
                            ("no timestamp", self.claim(at=None)),
                            ("a boolean round", self.claim(round=True))):
            with self.subTest(label):
                self.log(self.claim(), line)
                self.refuse("nothing-open", code=0)

    def test_a_diagnostics_log_that_is_a_symlink_is_an_environment_fault(self):
        """Row `_later_claim` open refusal. Positive control: the same log as a regular file is
        read. Mutation: read through the link -> `recognised`."""
        real = self.log(self.claim())
        link = self.env.run_dir / "linked.jsonl"
        link.write_bytes(real.read_bytes())
        real.unlink()
        real.symlink_to(link)
        before = self.frozen()
        with gateway_spy() as calls:
            rc, docs, err = self.recover()
        self.assertEqual((rc, docs, calls), (2, [], []), err)
        self.assertIn("diagnostics log", err)
        self.assertEqual(self.frozen(), before)


class RestoreRowTests(_Closed):

    def test_a_refused_owner_commit_puts_back_the_altered_record_it_overwrote(self):
        """Row `_restore_after_refusal`, `before != item.data`. The owner's remedy overwrites an
        altered working-tree record with the pending bytes, a hook then rejects the commit, and
        the altered bytes come back. Positive control: with the hook gone the remedy commits.
        Mutation: skip the restore -> the working tree keeps the pending bytes."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        altered = (json.dumps(json.loads(data), indent=4) + "\n").encode()
        path = self.put(self.rec_name(), altered)
        self.install_hook()
        before = self.frozen()
        with gateway_spy() as calls:
            rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual(calls, [])
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "hook-or-commit-failed", 1), err)
        self.assertEqual(path.read_bytes(), altered)
        self.assertEqual(self.frozen(), before)
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)

    def test_a_hook_that_deletes_the_record_and_fails_leaves_a_refusal_not_a_crash(self):
        """Row `_restore_after_refusal`, `except FileNotFoundError`. The hook removes the record
        recovery wrote. Positive control: the report names the hook's refusal and the pending copy
        survives. Mutation: drop the guard -> exit 2 `unexpected FileNotFoundError`."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.install_hook("#!/bin/sh\nfor f in $(git diff --cached --name-only); do\n"
                          "  case \"$f\" in *.attempt.json) ;; */council/*.json) rm -f \"$f\" ;; esac\n"
                          "done\nexit 1\n")
        before = self.frozen()
        with gateway_spy() as calls:
            rc, docs, err = self.recover()
        self.assertEqual(calls, [])
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "hook-or-commit-failed", 1), err)
        self.assertNotIn("unexpected", err)
        self.assertEqual(self.frozen(), before)


class ActErrorTests(_Closed):

    def setUp(self):
        super().setUp()
        self.att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(self.att))

    def act_raising(self, exc: BaseException):
        before = self.frozen()

        def act(ctx, item):
            raise exc

        with gateway_spy() as calls, mock.patch.object(rec, "_act", act):
            rc, docs, err = self.recover()
        self.assertEqual(calls, [])
        self.assertEqual(self.frozen(), before)
        return rc, docs, err

    def test_a_refusal_raised_while_acting_exits_1_naming_its_code(self):
        rc, docs, err = self.act_raising(council_commit.CommitRefused("hook-or-commit-failed", "refused"))
        self.assertEqual((rc, self.report(docs)["recovery"], self.report(docs)["code"]),
                         (1, "commit-refused", "hook-or-commit-failed"), err)
        self.assertIn("git refused (hook-or-commit-failed) while recovery acted on", err)
        self.assertIn("no earlier item was acted on", err)

    def test_an_unreadable_env_file_raised_while_acting_exits_2(self):
        rc, docs, err = self.act_raising(council_commit.EnvUnreadable())
        self.assertEqual((rc, docs), (2, []), err)
        self.assertIn("crux env file cannot be read", err)
        self.assertIn("while recovery acted on", err)

    def test_an_unexpected_error_raised_while_acting_exits_2_naming_its_type_only(self):
        """Mutation: print the message -> the key shape reaches stderr (and `recover` fails on it)."""
        rc, docs, err = self.act_raising(RuntimeError(KEY_SHAPED))
        self.assertEqual((rc, docs), (2, []), err)
        self.assertIn("unexpected RuntimeError while recovery acted on", err)
        self.assertNotIn(KEY_SHAPED, err)


class CommitRefusalReportTests(_Closed):
    """What a refused commit that moved state outside the owned paths tells the owner."""

    def setUp(self):
        super().setUp()
        self.att = self.attempt()
        self.name = self.rec_name()
        self.data = self.record_bytes(self.att)
        self.pend(self.name, self.data)
        self.rel = self.env.rel(self.env.council / self.name)

    def refused_by(self, exc_factory, *, lands: bool = False):
        real = council_commit.commit_owned

        def shim(*a, **k):
            if lands:
                real(*a, **k)
            raise exc_factory()

        with gateway_spy() as calls, mock.patch.object(council_commit, "commit_owned", shim):
            rc, docs, err = self.recover()
        self.assertEqual(calls, [])
        return rc, self.report(docs), err

    def test_a_timeout_names_the_moved_paths_the_staged_paths_and_the_remedy_order(self):
        """Positive control: the same refusal with nothing moved carries neither key. Mutation: drop
        the moved and staged paths from the report -> the keys are absent. The remedy comes in the
        owner's order: find the set-aside work, then remove a stale lock, then run recovery."""
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused(
            "timeout", "the call exceeded its bound", moved=("src/app.py", "refs/stash"), staged=(self.rel,)))
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "timeout", 1), err)
        self.assertEqual(rep["outside_moved"], ["src/app.py", "refs/stash"])
        self.assertEqual(rep["owned_staged"], [self.rel])
        for needle in ("src/app.py", "refs/stash"):
            self.assertIn(needle, err)
        self.assertIn(". The call found these outside paths changed", err)  # a new sentence, capitalised
        self.assertNotIn(". the call found", err)
        order = [err.index(s) for s in ("git stash list", "backup patch", "index.lock", "run recovery")]
        self.assertEqual(order, sorted(order), err)
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused("timeout", "the call exceeded"))
        self.assertNotIn("outside_moved", rep)
        self.assertNotIn("owned_staged", rep)
        self.assertIn("git stash list", err)

    def test_an_outside_state_that_was_not_compared_is_named_as_such(self):
        """`council_commit.MOVED_UNKNOWN` is a marker, not a path. Red when stderr prints it as a
        changed path."""
        unknown = council_commit.MOVED_UNKNOWN
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused(
            "timeout", "the call exceeded its bound", moved=(unknown,)))
        self.assertEqual((rep["code"], rc), ("timeout", 1), err)
        self.assertNotIn(f"changed: {unknown}", err)
        for needle in ("could not compare", "git status", "git stash list"):
            self.assertIn(needle, err)

    def test_a_failed_commit_that_moved_outside_work_names_it_with_the_remedy_order(self):
        """`council_commit` now compares the outside state after a failed commit too, so moved paths
        on `hook-or-commit-failed` mean a hook set the user's work aside and failed before its
        restore. Red when the remedy order is attached to a timeout only: stderr names the moved
        path but no stash step. Positive control: the same refusal with nothing moved carries no
        stash step."""
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused(
            "hook-or-commit-failed", "a hook failed", moved=("src/app.py",)))
        self.assertEqual((rep["code"], rep["outside_moved"], rc), ("hook-or-commit-failed", ["src/app.py"], 1), err)
        self.assertIn("git stash list", err)
        order = [err.index(s) for s in ("src/app.py", "git stash list", "index.lock", "run recovery")]
        self.assertEqual(order, sorted(order), err)
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused("hook-or-commit-failed", "a hook failed"))
        self.assertNotIn("outside_moved", rep)
        self.assertNotIn("git stash list", err)

    def outside_clause_once(self, code: str, *, lands: bool) -> None:
        moved = ("src/app.py",)
        detail = council_commit._outside_detail("a hook failed", moved, (), timed_out=code == "timeout")
        self.assertIn("git stash list", detail)  # the control: the detail does carry the clause
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused(
            code, detail, moved=moved, base="a hook failed"), lands=lands)
        self.assertEqual((rep["code"], rep.get("commit_landed", False), rc), (code, lands, 1), err)
        self.assertEqual(err.count("git stash list"), 1, err)
        self.assertEqual(err.count("src/app.py"), 1, err)
        self.assertIn("a hook failed", err)

    def test_a_refusal_whose_detail_carries_the_outside_clause_prints_the_remedy_and_each_path_once(self):
        """`council_commit` puts the moved paths and the remedy order into the refusal's `detail`
        and keeps the stop alone in `base`. Red when recovery builds its reason from `detail`:
        stderr names `git stash list` and the moved path twice. Positive control: each still prints."""
        for code in ("hook-or-commit-failed", "timeout"):
            with self.subTest(code=code):
                self.outside_clause_once(code, lands=False)

    def test_a_landed_failed_commit_with_moved_work_prints_the_remedy_and_each_path_once(self):
        """Red when the reason comes from `detail`: the moved path prints twice. Red when the
        landed branch adds the remedy only for a timeout: no stash step once the reason is `base`."""
        self.outside_clause_once("hook-or-commit-failed", lands=True)

    def test_a_landed_timeout_with_moved_work_prints_the_remedy_and_each_path_once(self):
        """Red when the reason comes from `detail`: the remedy and the moved path print twice."""
        self.outside_clause_once("timeout", lands=True)

    def test_a_landed_timeout_names_the_moved_paths_and_the_remedy_order(self):
        head0 = self.head()
        rc, rep, err = self.refused_by(lambda: council_commit.CommitRefused(
            "timeout", "the call exceeded its bound", moved=("src/app.py",)), lands=True)
        self.assertNotEqual(self.head(), head0)
        self.assertEqual((rep["commit_landed"], rep["outside_moved"], rc), (True, ["src/app.py"], 1), err)
        order = [err.index(s) for s in ("git stash list", "index.lock", "run recovery")]
        self.assertEqual(order, sorted(order), err)

    def test_a_moved_path_shaped_like_a_key_is_withheld(self):
        before = self.frozen()
        with mock.patch.object(council_commit, "commit_owned", side_effect=council_commit.CommitRefused(
                "timeout", "x", moved=(f"leak/{KEY_SHAPED}.txt",))):
            rc, docs, err = self.recover()  # `recover` fails when a key shape reaches either stream
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.report(docs)["recovery"], "commit-refused")
        self.assertEqual(self.frozen(), before)

    def test_one_bad_moved_name_is_labelled_alone_and_the_remedy_order_survives(self):
        """A key-shaped name is withheld on its own and a control character is shown as `?`, so the
        other moved path and the remedy order still print. Red when the stderr line is scanned only
        as a whole: the key-shaped name withholds the whole line, remedy and all, or the escape byte
        reaches stderr."""
        with mock.patch.object(council_commit, "commit_owned", side_effect=council_commit.CommitRefused(
                "timeout", "x", moved=(f"leak/{KEY_SHAPED}.txt", "esc\x1bname.txt", "src/b.txt"),
                staged=("own\x1bstaged.json",))):
            rc, docs, err = self.recover()  # `recover` fails when a key shape reaches either stream
        self.assertEqual(rc, 1, err)
        self.assertNotIn(KEY_SHAPED, err)
        self.assertNotIn("\x1b", err)
        for needle in ("withheld", "esc?name.txt", "src/b.txt", "own?staged.json", "git stash list", "index.lock"):
            self.assertIn(needle, err)


class UsageErrorTests(_Base):

    def test_a_usage_error_does_not_echo_a_key_shaped_argument(self):
        """Argparse quotes the offending value. Positive control: a harmless bad value is echoed
        in the message (last block). Mutation: argparse's own `error` -> the key shape reaches stderr."""
        for argv in (["run.yaml", "--prompt", KEY_SHAPED], ["run.yaml", KEY_SHAPED, "--bogus-" + KEY_SHAPED],
                     ["run.yaml", f"--prompt={KEY_SHAPED}"]):
            with self.subTest(argv[-1][:12]):
                import io
                out, errio = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(errio):
                    rc = rec.main(argv)
                self.assertEqual(rc, 2)
                self.assertNotIn(KEY_SHAPED, out.getvalue() + errio.getvalue())
                self.assertTrue(errio.getvalue().strip(), "a usage error must still say something")
        import io
        errio = io.StringIO()
        with contextlib.redirect_stderr(errio):
            rec.main(["run.yaml", "--prompt", "abc"])
        self.assertIn("abc", errio.getvalue())


class HelpTests(unittest.TestCase):

    def help_text(self) -> str:
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            rec._parser().parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)
        return out.getvalue()

    def test_help_lists_the_exit_codes_the_result_tokens_and_the_prompt_flag(self):
        text = self.help_text()
        for token in rec.TOKENS:
            self.assertIn(token, text)
        for code in ("0", "1", "2"):
            self.assertRegex(text, rf"(?m)^\s*{code}\s+\S")
        self.assertIn("--prompt <n>", text)
        self.assertIn("nothing-open", text)
        self.assertRegex(text, r"(?s)without .{0,40}--prompt.{0,200}nothing-open|nothing-open.{0,300}--prompt")
        # A recognised or committed record exits with its own code: 1 also for a ran record the
        # secret scan reduced (council_records.record_exit_code), not only a could-not-run record.
        self.assertRegex(" ".join(text.split()), r"ran record (?:whose full write )?the secret scan (?:refused|reduced)")

    def test_no_message_or_docstring_says_preflight_record_without_could_not_run(self):
        import re
        sources = [Path(rec.__file__).read_text(encoding="utf-8")]
        for src in sources:
            flat = " ".join(src.replace("#", " ").split())
            self.assertIsNone(re.search(r"preflight records?(?! pending| could)", flat), re.findall(r".{20}preflight record.{20}", flat))

    def test_the_commit_bound_is_the_shared_constant(self):
        env = {}
        with mock.patch.object(council_commit, "GIT_TIMEOUT_SECONDS", 7.0):
            self.assertEqual(rec.Git(Path("."), env).timeout, 7.0)
        self.assertEqual(rec.Git(Path("."), env).timeout, council_commit.GIT_TIMEOUT_SECONDS)


class PendingWalkTests(_Closed):

    def test_a_pending_copy_read_through_an_interior_symlink_is_not_read(self):
        """`_pending_bytes` reads one pending copy the way `council_records.open_pending_dir` walks:
        every component from the git directory with O_NOFOLLOW. Positive control: the real
        directory is read. Mutation: read by path -> the bytes come back through the link."""
        att = self.attempt()
        data = self.record_bytes(att)
        path = self.pend(self.rec_name(), data)
        args = (self.root, sup.BOOK_ID, sup.RUN_ID, self.rec_name())
        self.assertEqual(rec._pending_bytes(*args), data)
        book_dir = path.parent.parent
        moved = self.tmp / "elsewhere"
        shutil.move(str(book_dir), str(moved))
        book_dir.symlink_to(moved)
        self.assertEqual((moved / sup.RUN_ID / self.rec_name()).read_bytes(), data)
        self.assertIsNone(rec._pending_bytes(*args))

    def test_a_pending_copy_that_is_a_symlink_is_not_read(self):
        att = self.attempt()
        data = self.record_bytes(att)
        path = self.pend(self.rec_name(), data)
        target = self.tmp / "target.json"
        target.write_bytes(data)
        path.unlink()
        path.symlink_to(target)
        self.assertIsNone(rec._pending_bytes(self.root, sup.BOOK_ID, sup.RUN_ID, self.rec_name()))

    def test_a_refused_commit_reports_a_pending_directory_swapped_for_a_symlink(self):
        """End to end: the pending directory becomes a symlink while the commit runs, and the
        commit is refused. The report says the pending copy moved, because the copy cannot be read
        without following the link. Mutation: read by path -> `pending_moved` is absent."""
        att = self.attempt()
        data = self.record_bytes(att)
        path = self.pend(self.rec_name(), data)
        book_dir = path.parent.parent
        moved = self.tmp / "elsewhere"

        def swap_then_refuse(*a, **k):
            shutil.move(str(book_dir), str(moved))
            book_dir.symlink_to(moved)
            raise council_commit.CommitRefused("hook-or-commit-failed", "a hook failed")

        with gateway_spy() as calls, mock.patch.object(council_commit, "commit_owned", swap_then_refuse):
            rc, docs, err = self.recover()
        self.assertEqual(calls, [])
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rc), ("commit-refused", 1), err)
        self.assertTrue(rep.get("pending_moved"), rep)


class RunCouncilRouteTests(_Base):
    """`run-council.py --recover` loads the gateway modules and never calls them."""

    def child_env(self) -> dict:
        env = sup.scrubbed_env()
        env.pop("OPENROUTER_API_KEY", None)
        env.update(CRUX_HOME=str(self.crux_home))
        return env

    def shim(self, *argv: str, extra: tuple = ()) -> tuple:
        counter = self.tmp / "requests.jsonl"
        cmd = [sys.executable, str(SHIM), "--counter", str(counter), "--deadline", "60", *extra, "--", *argv]
        proc = subprocess.run(cmd, cwd=self.root, env=self.child_env(), capture_output=True, text=True, timeout=120)
        count = len([ln for ln in counter.read_text().splitlines() if ln.strip()]) if counter.exists() else 0
        return proc, count

    def test_the_counter_counts_a_request(self):
        """Positive control: the shim's own check makes three requests and the counter sees them."""
        proc, count = self.shim(extra=("--self-check",))
        self.assertEqual((proc.returncode, count), (0, 3), proc.stderr)

    def test_the_documented_route_commits_with_no_key_and_makes_no_request(self):
        """The pending copy commits through `run-council.py --recover` with no key in the
        environment or the crux env file. Positive control: the head moved and the record is
        committed, so recovery ran its commit path. Mutation: a call into the gateway -> the counter
        is above 0 (or the call fails for want of a key)."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        head0 = self.head()
        proc, count = self.shim("--recover", str(self.env.run_path), "--prompt", str(PROMPT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json_docs(proc.stdout)[0]["recovery"], "committed", proc.stdout + proc.stderr)
        self.assertEqual(count, 0, "recovery made a gateway request")
        self.assertNotEqual(self.head(), head0)
        self.assertEqual(self.head_blob(self.env.rel(self.env.council / self.rec_name())), data)

    def test_the_documented_route_reports_an_open_attempt_with_no_key_and_makes_no_request(self):
        att = self.attempt()
        proc, count = self.shim("--recover", str(self.env.run_path), "--prompt", str(PROMPT))
        self.assertEqual((proc.returncode, count), (1, 0), proc.stdout + proc.stderr)
        self.assertEqual(json_docs(proc.stdout)[0], {"recovery": "unproven", "attempt": self.env.rel(att),
                                                     "record": None})


if __name__ == "__main__":
    unittest.main()
