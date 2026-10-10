"""`council_recovery`: recovery from a council persistence failure, decided from git and the tree.

Each class covers one row of the recovery decision table, and each test names its positive control
and the mutation that would turn it green wrongly:

- (a) recognised: a council record in HEAD names the attempt, its seal holds, and it equals the
  pending copy. Recovery reprints the summary, removes the pending copy, and exits with the
  record's code. It makes no commit.
- (b) committed: a pending copy whose bytes are not in HEAD, with no working-tree record or one
  equal to it. Recovery re-scans, commits exactly the pending bytes (and any retained copy whose
  bytes match its subject hash), verifies them, and removes the pending copy.
- (c) mismatch: a working-tree record that differs from the pending copy, conflicting pending
  copies, a failing seal, or HEAD differing from the pending copy. Recovery changes nothing.
  `--owner-commit-pending` commits the pending bytes over the altered record, and acts on a
  mismatch only.
- (d) unproven: a working-tree record with no pending copy and not in HEAD, or no record at all.
- (e) index-locked / live-runner: checked before any write; recovery changes nothing.
- (f) released: an uncommitted claim whose lock is free is deleted and unstaged.

Also: `nothing-open`, `--probe`, a hook-rejected recovery commit, environment faults (exit 2), and
a child process proving that the recovery module alone leaves `crux.council` and
`crux.core.llm_caller` unloaded, reads no key and opens no socket. The route `run-council.py
--recover`, which loads those modules and never calls them, is covered in
`test_council_recovery_failclosed.py` (`RunCouncilRouteTests`).

Every case builds a temporary git repository with `_council_gate_support.Env` and an isolated git
configuration, and a temporary empty `CRUX_HOME`. Nothing reads the machine's git configuration,
a real key, the network, or anything outside `crux/` and the temporary directory.
"""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
import _council_gate_support as sup  # noqa: E402

import council_commit  # noqa: E402
import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402
import crux_env  # noqa: E402

try:
    import council_recovery as rec  # noqa: E402
except ImportError:  # the module under test does not exist yet: every test fails on it
    rec = None

RECOVERY = SCRIPTS / "council_recovery.py"
PROMPT = 2
TAG = "adr-1"
KEY_SHAPED = "sk-or-v1-" + "ab" * 24
REJECT_HOOK = "#!/bin/sh\necho 'hook: refusing every commit' >&2\nexit 1\n"
#: A pre-commit hook that rewrites each staged council record (never an attempt record) as a
#: content-preserving reformat and stages the result: the commit lands, with bytes that differ
#: from the pending copy.
REFORMAT_HOOK = textwrap.dedent('''\
    #!{python}
    import json, subprocess
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"], capture_output=True, check=True).stdout
    for name in [n.decode() for n in out.split(b"\\0") if n]:
        if "/council/" in name and name.endswith(".json") and not name.endswith(".attempt.json"):
            with open(name, encoding="utf-8") as fh:
                doc = json.load(fh)
            with open(name, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, indent=4) + "\\n")
            subprocess.run(["git", "add", "--", name], check=True)
''').format(python=sys.executable)


def json_docs(text: str) -> list:
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


class _Base(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(rec, "council_recovery cannot be imported")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.crux_home = self.tmp / "crux-home"
        self.crux_home.mkdir()
        env = dict(sup.isolated_git_config(self.tmp / "cfg"), CRUX_HOME=str(self.crux_home))
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        crux_env._reset_cache()
        self.addCleanup(crux_env._reset_cache)
        self.env = sup.Env(self.tmp / "repo", base=True)
        self.root = self.env.root

    # -- builders ---------------------------------------------------------------------------

    def attempt(self, commit: bool = True, **kw) -> Path:
        name = self.env.attempt_name(**{k: kw[k] for k in ("module_tag", "prompt", "round", "ordinal") if k in kw})
        return self.env.write_sealed(name, self.env.attempt_doc(**kw), commit=commit)

    def record_bytes(self, att: Path, mutate=None, **kw) -> bytes:
        doc = self.env.council_v2_doc(att, **kw)
        if mutate:
            mutate(doc)
        return cr.canonical_bytes(cr.sealed(doc))

    def rec_name(self, round_: int = 1) -> str:
        return f"{sup.RUN_ID}-p{PROMPT}-r{round_}-20261002T120000000001Z.json"

    def pend(self, name: str, data: bytes) -> Path:
        return council_commit.write_pending(self.root, sup.BOOK_ID, sup.RUN_ID, name, data)

    def put(self, name: str, data: bytes, commit: bool = False) -> Path:
        path = self.env.council / name
        path.write_bytes(data)
        if commit:
            self.env.commit_records(path)
        return path

    def install_hook(self, text: str = REJECT_HOOK) -> None:
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(text)
        hook.chmod(0o755)

    # -- running recovery -------------------------------------------------------------------

    def recover(self, *extra: str, prompt: int | None = PROMPT):
        argv = [str(self.env.run_path)]
        if prompt is not None:
            argv += ["--prompt", str(prompt)]
        argv += list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rec.main(argv)
        docs = json_docs(out.getvalue())
        self.assertNotIn(KEY_SHAPED, out.getvalue() + err.getvalue())
        return rc, docs, err.getvalue()

    def report(self, docs: list) -> dict:
        self.assertTrue(docs and isinstance(docs[0], dict) and "recovery" in docs[0], docs)
        return docs[0]

    # -- git --------------------------------------------------------------------------------

    def head(self) -> str:
        return sup.git(self.root, "rev-parse", "HEAD").strip()

    def head_blob(self, rel: str) -> bytes | None:
        r = subprocess.run(["git", "-C", str(self.root), "cat-file", "blob", f"HEAD:{rel}"],
                           capture_output=True, env=sup.scrubbed_env())
        return r.stdout if r.returncode == 0 else None

    def index(self) -> bytes:
        return subprocess.run(["git", "-C", str(self.root), "ls-files", "-s"], capture_output=True,
                              env=sup.scrubbed_env(), check=True).stdout

    def files_of(self, sha: str) -> list[str]:
        return sorted(sup.git(self.root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split())

    def pending(self) -> dict[str, bytes]:
        return cg.read_pending(self.root, self.env.run)

    def state(self, att: Path) -> cg.AttemptState:
        rel = self.env.rel(att)
        found = [s for s in cg.attempt_states(self.env.run, self.env.run_dir, self.root, TAG, PROMPT)
                 if s.rel == rel]
        self.assertEqual(len(found), 1)
        return found[0]

    def frozen(self) -> tuple:
        """HEAD, the index, the council directory's files and the pending copies."""
        files = {p.name: p.read_bytes() for p in self.env.council.iterdir() if p.is_file()}
        return self.head(), self.index(), files, self.pending()


# ───────────────────────────── (a) recognised ─────────────────────────────


class RecognisedTests(_Base):

    def test_a_committed_record_equal_to_its_pending_copy_is_recognised_without_a_commit(self):
        """Positive control: the runner died after its verified commit, before removing the pending
        copy. Recovery reprints the summary naming the record, removes the pending copy, makes no
        commit, and exits 0. Mutation: recovery re-commits -> HEAD moves."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), data, commit=True)
        head0 = self.head()
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rc), ("recognised", 0), err)
        self.assertEqual(rep, {"recovery": "recognised", "attempt": self.env.rel(att),
                               "record": self.env.rel(path)})
        self.assertEqual(docs[1]["record"], self.env.rel(path), "the summary was not reprinted")
        self.assertEqual(docs[1]["attempt"], self.env.rel(att))
        self.assertEqual(docs[1]["outcome"], "ran")
        self.assertEqual(self.head(), head0)
        self.assertEqual(self.pending(), {})

    def test_a_recognised_could_not_run_record_exits_with_its_own_code(self):
        """The exit code is the record's: a could-not-run record exits 1 even when recognised.
        Mutation: always exit 0 on recognised."""
        att = self.attempt()
        data = self.record_bytes(att, outcome="could-not-run")
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), data, commit=True)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 1), err)
        self.assertEqual(docs[1]["outcome"], "could-not-run")

    def test_head_differing_from_the_pending_copy_is_a_mismatch_not_recognised(self):
        """Mutation of the positive control above: HEAD holds a content-preserving reformat of the
        pending copy, while the working tree holds the pending bytes again, so HEAD alone differs.
        Recovery trusting HEAD (or the working tree) would say recognised; it must say mismatch for
        that reason, exit 1, and keep the pending copy."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), (json.dumps(json.loads(data), indent=4) + "\n").encode(), commit=True)
        path.write_bytes(data)
        self.assertNotEqual(self.head_blob(self.env.rel(path)), data)
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertIn("HEAD's record differs from the pending copy", err)
        self.assertEqual(self.frozen(), before)

    def test_two_committed_records_naming_one_attempt_are_a_mismatch_not_recognised(self):
        """Two committed records name the attempt with the pending copy's bytes, so the gate
        resolves nothing. Positive control: with one record the same state is recognised (the first
        test). Mutation: skip the re-check of the attempt's state -> recognised, pending removed."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), data, commit=True)
        self.put(self.rec_name().replace("120000", "130000"), data, commit=True)
        self.assertTrue(self.state(att).open)
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertIn("not resolved", err)
        self.assertEqual(self.frozen(), before)

    def test_a_resolved_attempt_whose_pending_copy_is_gone_is_recognised_at_its_running_prompt(self):
        """The runner died after removing its pending copy and before printing. Recovery invoked
        with --prompt for the prompt the record is bound to, while that prompt is still running,
        reprints the record and exits with its code. It makes no commit. Positive control: the
        same state with the prompt done is nothing-open (NothingOpenTests). Mutation: drop the
        fallback -> nothing-open, and the conductor never learns the outcome."""
        att = self.attempt()
        path = self.put(self.rec_name(), self.record_bytes(att, outcome="could-not-run"), commit=True)
        self.assertEqual(self.state(att).state, "resolved")
        self.assertEqual(self.pending(), {})
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual(self.report(docs), {"recovery": "recognised", "attempt": self.env.rel(att),
                                             "record": self.env.rel(path)})
        self.assertEqual(rc, 1, err)
        self.assertEqual((docs[1]["record"], docs[1]["outcome"]), (self.env.rel(path), "could-not-run"))
        self.assertEqual(self.frozen(), before)


# ───────────────────────────── (b) committed ─────────────────────────────


class CommittedTests(_Base):

    def assert_recovery_commit(self, head0: str, rels: list[str]) -> None:
        commits = sup.git(self.root, "rev-list", f"{head0}..HEAD").split()
        self.assertEqual(len(commits), 1, commits)
        self.assertEqual(self.files_of(commits[0]), sorted(rels))
        subject = sup.git(self.root, "log", "-1", "--format=%s").strip()
        self.assertTrue(subject.startswith(f"crux council recovery: {sup.BOOK_ID} {sup.RUN_ID} prompt {PROMPT} "
                                           "round 1"), subject)

    def test_a_pending_copy_with_no_working_tree_record_is_committed_and_resolves_the_attempt(self):
        """The runner's record commit failed and the working-tree record was deleted. Recovery
        writes the pending bytes, commits exactly them, verifies, removes the pending copy, and
        the attempt is resolved. Positive control: before recovery the attempt is open.
        Mutation: recovery commits nothing -> the attempt stays open."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.assertTrue(self.state(att).open)
        rel = self.env.rel(self.env.council / self.rec_name())
        head0 = self.head()
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rc), ("committed", 0), err)
        self.assertEqual(rep, {"recovery": "committed", "attempt": self.env.rel(att), "record": rel})
        self.assertEqual(docs[1]["record"], rel)
        self.assertEqual(self.head_blob(rel), data)
        self.assertEqual((self.root / rel).read_bytes(), data)
        self.assert_recovery_commit(head0, [rel])
        self.assertEqual(self.pending(), {})
        self.assertEqual(self.state(att).state, "resolved")

    def test_a_working_tree_record_equal_to_the_pending_copy_is_committed(self):
        att = self.attempt()
        data = self.record_bytes(att, outcome="could-not-run")
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), data)
        head0 = self.head()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 1), err)
        self.assert_recovery_commit(head0, [self.env.rel(path)])
        self.assertEqual(self.state(att).state, "resolved")

    def test_a_retained_copy_is_committed_only_when_its_bytes_match_its_subject_hash(self):
        """Positive control: a retained copy equal to the subject is committed with the record.
        Mutation: a second retained copy whose bytes differ from its subject hash stays out."""
        att = self.attempt()

        def retain(doc):
            doc["subjects"][0]["retained_copy"] = "council/subjects/good.md"
            doc["subjects"][1]["retained_copy"] = "council/subjects/bad.md"

        doc_att = json.loads(att.read_bytes())
        doc_att["subjects"].append(dict(doc_att["subjects"][0]))
        doc_att["seal"] = None
        att.write_bytes(cr.canonical_bytes(cr.sealed(doc_att)))
        self.env.commit_records(att)
        data = self.record_bytes(att, mutate=retain)
        subjects = self.env.council / "subjects"
        subjects.mkdir()
        (subjects / "good.md").write_bytes(self.env.subject.read_bytes())
        (subjects / "bad.md").write_bytes(b"not the subject\n")
        self.pend(self.rec_name(), data)
        head0 = self.head()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        rel = self.env.rel(self.env.council / self.rec_name())
        self.assert_recovery_commit(head0, [rel, self.env.rel(subjects / "good.md")])
        self.assertIsNone(self.head_blob(self.env.rel(subjects / "bad.md")))

    def test_a_preflight_record_pending_copy_is_committed(self):
        """A preflight could-not-run record names no attempt; its pending copy is still recovered."""
        data = cr.canonical_bytes(cr.sealed(self.env.preflight_doc("preflight-retries-spent")))
        self.pend(self.rec_name(), data)
        rel = self.env.rel(self.env.council / self.rec_name())
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 1), err)
        self.assertEqual(self.report(docs)["attempt"], None)
        self.assertEqual(self.head_blob(rel), data)
        self.assertEqual(self.pending(), {})
        subject = sup.git(self.root, "log", "-1", "--format=%s").strip()
        self.assertEqual(subject, f"crux council recovery: {sup.BOOK_ID} {sup.RUN_ID} prompt {PROMPT} round 1")

    def test_a_committed_pending_copy_that_leaves_the_attempt_unresolved_is_a_mismatch(self):
        """A second committed record already names the attempt with the same bytes. Recovery
        commits the pending copy, re-reads the attempt's state, finds two resolving records, and
        reports a mismatch, keeping the pending copy. Mutation: trust the commit alone ->
        committed, pending removed, while the gate resolves nothing."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.put(self.rec_name().replace("120000", "130000"), data, commit=True)
        self.pend(self.rec_name(), data)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertIn("not resolved", err)
        self.assertEqual(list(self.pending()), [self.rec_name()])
        self.assertTrue(self.state(att).open)

    def test_a_pending_copy_carrying_a_key_shape_is_not_committed(self):
        """The re-scan before the commit. Positive control: the clean copy commits (the first test).
        Mutation: skip the re-scan -> the key-shaped record is committed."""
        att = self.attempt()

        def leak(doc):
            doc["seats"][0]["reasoning"] = f"the key {KEY_SHAPED} leaked"

        data = self.record_bytes(att, mutate=leak)
        self.pend(self.rec_name(), data)
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("unproven", 1), err)
        self.assertIn("secret scan", err)
        self.assertEqual(self.frozen(), before)


# ───────────────────────────── (c) mismatch ─────────────────────────────


class MismatchTests(_Base):

    def test_a_working_tree_record_differing_from_the_pending_copy_is_a_mismatch(self):
        """Positive control: the equal working-tree record commits (CommittedTests). Mutation:
        recovery trusts the working tree -> it commits the altered bytes."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), (json.dumps(json.loads(data), indent=4) + "\n").encode())
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertEqual(self.frozen(), before)
        self.assertTrue(self.state(att).open)

    def test_conflicting_pending_copies_are_a_mismatch(self):
        """Two pending copies name one attempt. Mutation: commit the first -> committed."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        self.pend(self.rec_name().replace("120000", "130000"),
                  self.record_bytes(att, decisions=("APPROVE", "REQUEST_CHANGES", "APPROVE")))
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertEqual(self.frozen(), before)

    def test_a_pending_copy_whose_seal_fails_is_a_mismatch(self):
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data.replace(b"\n", b"\r\n"))
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertEqual(self.frozen(), before)
        # Even the owner's remedy refuses a copy whose seal fails.
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertEqual(self.frozen(), before)

    def test_owner_commit_pending_commits_the_pending_bytes_over_a_hook_altered_record(self):
        """The owner's remedy after fixing the hook. Positive control: without the flag, the same
        state is a mismatch that changes nothing. Mutation: the flag commits the HEAD bytes."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        path = self.put(self.rec_name(), (json.dumps(json.loads(data), indent=4) + "\n").encode(), commit=True)
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        self.assertEqual(self.frozen(), before)

        head0 = self.head()
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        self.assertEqual(self.head_blob(self.env.rel(path)), data)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(len(sup.git(self.root, "rev-list", f"{head0}..HEAD").split()), 1)
        self.assertEqual(self.pending(), {})
        self.assertEqual(self.state(att).state, "resolved")

    def test_owner_commit_pending_acts_on_a_mismatch_only(self):
        """With no mismatch the flag changes nothing: a recognised record stays recognised with no
        commit, an unproven attempt stays unproven, and nothing open stays nothing open."""
        rc, docs, _ = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0))
        att = self.attempt()
        before = self.frozen()
        rc, docs, _ = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("unproven", 1))
        self.assertEqual(self.frozen(), before)
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.put(self.rec_name(), data, commit=True)
        head0 = self.head()
        rc, docs, _ = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0))
        self.assertEqual(self.head(), head0)


# ───────────────────────────── (d) unproven ─────────────────────────────


class UnprovenTests(_Base):

    def test_an_attempt_with_no_council_record_is_unproven(self):
        """The runner died after a request. Mutation: report nothing-open -> the owner is not told."""
        att = self.attempt()
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual(self.report(docs), {"recovery": "unproven", "attempt": self.env.rel(att), "record": None})
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.frozen(), before)
        self.assertTrue(self.state(att).open)

    def test_a_working_tree_record_with_no_pending_copy_is_unproven_and_not_committed(self):
        """Positive control: the same record with its pending copy commits (CommittedTests).
        Mutation: commit a working-tree record with no witness of its original bytes."""
        att = self.attempt()
        path = self.put(self.rec_name(), self.record_bytes(att))
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual(self.report(docs), {"recovery": "unproven", "attempt": self.env.rel(att),
                                             "record": self.env.rel(path)})
        self.assertEqual(rc, 1, err)
        self.assertEqual(self.frozen(), before)
        self.assertIsNone(self.head_blob(self.env.rel(path)))


# ───────────────────────────── (e) locks ─────────────────────────────


class LockTests(_Base):

    def hold(self, path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_a_held_index_lock_refuses_and_changes_nothing(self):
        """Positive control: removing the lock lets the same state commit. Mutation: check the lock
        after writing the working-tree record -> a file appears."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        lock = self.root / ".git" / "index.lock"
        lock.write_bytes(b"")
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("index-locked", 1), err)
        self.assertEqual(self.frozen(), before)
        lock.unlink()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)

    def test_a_live_runner_holding_its_attempt_lock_refuses_and_changes_nothing(self):
        """The test process holds the attempt's flock, as a live runner does. Positive control: once
        released, the same state commits. Mutation: skip the probe -> recovery commits under a
        live runner."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        fd = os.open(att, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            before = self.frozen()
            rc, docs, err = self.recover()
            self.assertEqual(self.report(docs), {"recovery": "live-runner", "attempt": self.env.rel(att),
                                                 "record": None})
            self.assertEqual(rc, 1, err)
            self.assertEqual(self.frozen(), before)
        finally:
            os.close(fd)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)

    def test_probe_reports_the_lock_state_and_the_open_attempts_and_writes_nothing(self):
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        before = self.frozen()
        rc, docs, err = self.recover("--probe")
        self.assertEqual(rc, 0, err)
        self.assertEqual(docs, [{"recovery": "probe", "lock": "free", "attempt": None, "record": None,
                                 "open": [self.env.rel(att)]}])
        self.hold(att)
        rc, docs, err = self.recover("--probe")
        self.assertEqual(rc, 1, err)
        self.assertEqual(docs[0]["lock"], "live-runner")
        self.assertEqual(docs[0]["attempt"], self.env.rel(att))
        self.assertEqual(self.frozen(), before)

    def test_a_sequence_in_progress_refuses_the_commit_and_changes_nothing(self):
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        before = self.frozen()
        (self.root / ".git" / "MERGE_HEAD").write_text(self.head() + "\n")
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "sequence-in-progress", 1), err)
        self.assertEqual(self.frozen(), before)


# ───────────────────────────── (f) released ─────────────────────────────


class ReleaseTests(_Base):

    def test_an_uncommitted_claim_with_a_free_lock_is_released(self):
        """The attempt commit failed. Mutation: leave the claim -> it holds the scope forever."""
        att = self.attempt(commit=False)
        head0 = self.head()
        rc, docs, err = self.recover()
        self.assertEqual(self.report(docs), {"recovery": "released", "attempt": self.env.rel(att), "record": None})
        self.assertEqual(rc, 0, err)
        self.assertFalse(att.exists())
        self.assertEqual(self.head(), head0)

    def test_a_staged_uncommitted_claim_is_released_and_unstaged(self):
        att = self.attempt(commit=False)
        sup.git(self.root, "add", "--", self.env.rel(att))
        self.assertIn(self.env.rel(att).encode(), self.index())
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("released", 0), err)
        self.assertFalse(att.exists())
        self.assertNotIn(self.env.rel(att).encode(), self.index())

    def test_a_claim_a_hook_rewrote_after_staging_is_released_and_unstaged(self):
        """A commit hook reformatted the staged claim file and failed the claim commit, so the staged
        bytes differ from both the file and HEAD. Red when the release unstages without `-f`: git
        refuses, recovery reports `commit-refused`, and the claim holds the scope for good.
        Positive control: before the rewrite, the index holds the claim's own bytes."""
        att = self.attempt(commit=False)
        rel = self.env.rel(att)
        sup.git(self.root, "add", "--", rel)
        self.assertIn(rel.encode(), self.index())
        staged = sup.git(self.root, "ls-files", "-s", "--", rel)
        att.write_text(json.dumps(json.loads(att.read_text()), indent=4) + "\n")  # the hook's rewrite
        self.assertEqual(sup.git(self.root, "ls-files", "-s", "--", rel), staged)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("released", 0), err)
        self.assertFalse(att.exists())
        self.assertNotIn(rel.encode(), self.index())

    def test_an_uncommitted_claim_held_by_a_live_runner_is_not_released(self):
        """Positive control: the first test releases the same claim when its lock is free."""
        att = self.attempt(commit=False)
        fd = os.open(att, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("live-runner", 1), err)
        self.assertTrue(att.exists())


# ───────────────────────────── nothing open ─────────────────────────────


class NothingOpenTests(_Base):

    def test_no_attempt_is_nothing_open(self):
        rc, docs, err = self.recover()
        self.assertEqual(docs, [{"recovery": "nothing-open", "attempt": None, "record": None}])
        self.assertEqual(rc, 0, err)

    def test_a_resolved_attempt_with_no_pending_copy_is_nothing_open_once_its_prompt_is_advanced(self):
        """Positive control: the same state at a running prompt is recognised (RecognisedTests).
        Mutation: ignore the prompt's state -> an advanced prompt's record is reprinted."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        self.assertEqual(self.state(att).state, "resolved")
        for state in ("done", "blocked"):
            self.env.set_run(2, {2: state})
            rc, docs, err = self.recover()
            self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_a_resolved_attempt_needs_an_explicit_prompt_to_be_recognised(self):
        """Without --prompt (current_prompt is 2), the same running-prompt state is nothing-open."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        rc, docs, err = self.recover(prompt=None)
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_a_resolved_attempt_bound_to_another_prompt_is_nothing_open(self):
        """The module convenes at prompts 2 and 3. Recovery for prompt 3 does not reprint prompt 2's
        record."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        self.env.set_run(3)
        rc, docs, err = self.recover(prompt=3)
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_an_earlier_round_is_not_recognised_past_a_later_attempt(self):
        """Round 1 is resolved and round 2's attempt was voided. The latest attempt in scope is
        round 2's, which no record resolves, so round 1 is not reprinted as the current outcome.
        Positive control: without the round-2 attempt, round 1 is recognised (RecognisedTests)."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        att2 = self.attempt(round=2)
        void = self.env.write(f"{sup.RUN_ID}-void.json", self.env.void_doc(att2), commit=True)
        self.assertEqual(self.state(att2).state, "voided", void)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)

    def test_an_earlier_round_is_not_recognised_past_a_later_committed_preflight_record(self):
        """Round 1 is resolved; a committed `preflight-retries-spent` record for round 2 at the same
        prompt is the later outcome, and it has no pending copy. Round 1's record is not the
        current outcome. Positive control: without the round-2 record, round 1 is recognised.
        Mutation: drop the later-record check -> round 1 is reprinted as `recognised`."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)
        later = self.rec_name(round_=2).replace("120000", "140000")
        self.put(later, cr.canonical_bytes(cr.sealed(self.env.preflight_doc("preflight-retries-spent", round=2))),
                 commit=True)
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)
        self.assertEqual(self.frozen(), before)

    def test_an_earlier_round_is_not_recognised_past_a_later_claim(self):
        """Round 1 is resolved; a later attempt was claimed and then released, so its only trace
        is the claim line in `council-preflight.jsonl`. Round 1's record is not the current
        outcome. Positive control: round 1's own claim line, written before its record, leaves it
        recognised. Mutation: drop the claim-line check -> round 1 is reprinted as `recognised`
        after a later round began."""
        att = self.attempt()
        self.put(self.rec_name(), self.record_bytes(att), commit=True)
        log = self.env.run_dir / "council-preflight.jsonl"

        def claim(round_: int, at: str) -> str:
            return json.dumps({"at": at, "event": "claim", "prompt": PROMPT, "round": round_, "codes": [],
                               "fields": []}) + "\n"

        own = claim(1, sup.ts(1, 0))  # round 1's claim, before its record's written_at
        other = json.dumps({"at": sup.ts(5), "event": "claim", "prompt": 3, "round": 4, "codes": [],
                            "fields": []}) + "\n"
        log.write_text(own + other)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)
        for label, line in (("a later round", claim(2, sup.ts(3))),
                            ("a later attempt at round 1", claim(1, sup.ts(4)))):
            with self.subTest(label):
                log.write_text(own + line + other)
                rc, docs, err = self.recover()
                self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)
        # Another run of the book shares the log: its later claim is not this run's.
        foreign = json.dumps({"at": sup.ts(4), "event": "claim", "prompt": PROMPT, "round": 2,
                              "run_id": "RUN-000", "codes": [], "fields": []}) + "\n"
        log.write_text(own + foreign + other)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)

    def test_an_attempt_in_another_scope_is_out_of_scope(self):
        """An open attempt at the verify module is not this adr prompt's. Positive control: the
        same attempt at adr-1 is unproven (UnprovenTests)."""
        self.attempt(module_tag="verify-1", prompt=6)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("nothing-open", 0), err)


# ───────────────────────────── a failed recovery commit ─────────────────────────────


class FailedRecoveryCommitTests(_Base):

    def test_a_hook_rejected_recovery_commit_leaves_index_head_and_pending_unchanged(self):
        """Positive control: with the hook removed the same recovery commits. Mutation: leave the
        written working-tree record or the staged path behind -> the frozen state differs."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.install_hook()
        before = self.frozen()
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "hook-or-commit-failed", 1), err)
        self.assertIn("hook-or-commit-failed", err)
        self.assertEqual(self.frozen(), before)
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)


    def test_a_hook_that_reformats_the_recovery_commit_reports_the_landed_commit(self):
        """The hook rewrites the record during recovery's commit: the commit lands and verification
        refuses it. Recovery must not claim HEAD is unchanged; it reports the landed commit, keeps
        the pending copy and leaves the attempt open. Positive control: the owner's remedy, once
        the hook is removed, commits the pending bytes. Mutation: report it as nothing landed ->
        the stderr claims HEAD is unchanged while HEAD moved."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.install_hook(REFORMAT_HOOK)
        head0 = self.head()
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep.get("code"), rep.get("commit_landed"), rc),
                         ("commit-refused", "mismatch", True, 1), err)
        self.assertNotEqual(self.head(), head0, "the positive control: the hook's commit landed")
        rel = self.env.rel(self.env.council / self.rec_name())
        self.assertNotEqual(self.head_blob(rel), data)
        self.assertNotIn("unchanged", err)
        self.assertIn("a commit landed", err)
        self.assertIn("--owner-commit-pending", err)
        self.assertEqual(list(self.pending()), [self.rec_name()])
        self.assertTrue(self.state(att).open)

        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("mismatch", 1), err)
        rc, docs, err = self.recover("--owner-commit-pending")
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        self.assertEqual(self.head_blob(rel), data)
        self.assertEqual(self.state(att).state, "resolved")

    def test_a_hook_that_alters_the_record_and_fails_leaves_the_index_and_tree_as_they_were(self):
        """The hook rewrites the working-tree record, then exits 1. `commit_owned` cannot unstage
        the path (git refuses: the staged bytes differ from both the file and HEAD). Recovery must
        not then delete the working-tree file under a staged entry: it unstages the entry, whose
        bytes are the pending copy's, removes the file it wrote, and reports only what holds.
        Positive control: with the hook removed the same recovery commits. Mutation: unlink the
        file without reading the index -> a staged new file with a deleted working copy, under a
        report that says the index is as it was."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        self.install_hook(REFORMAT_HOOK.rstrip("\n") + "\nraise SystemExit(1)\n")
        before = self.frozen()
        rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "hook-or-commit-failed", 1), err)
        self.assertNotIn("index_moved", rep)
        self.assertEqual(self.frozen(), before)
        self.assertIn("the index is as it was", err)
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        self.assertEqual(self.state(att).state, "resolved")

    def test_a_timed_out_recovery_commit_reports_the_left_lock_and_the_staged_path(self):
        """A pre-commit hook outlives the time bound and git ignores the graceful SIGINT (simulated
        by dropping SIGINT), so the SIGKILL after the grace leaves `index.lock` and the record stays
        staged. A git that honours SIGINT removes its own lock; this test pins the case where it
        does not. Recovery must not claim the index is as it was, and must not delete the
        working-tree record under the staged entry. Positive control: once the lock and the hook
        are gone, recovery commits the pending bytes. Mutation: report every refusal as 'nothing
        moved' -> no `index_moved`, and the stderr claims the index is as it was."""
        att = self.attempt()
        data = self.record_bytes(att)
        name = self.rec_name()
        self.pend(name, data)
        self.install_hook("#!/bin/sh\nsleep 30\n")
        head0 = self.head()
        real = council_commit.commit_owned
        real_killpg = council_commit.os.killpg

        def bounded(*a, **k):
            return real(*a, **dict(k, timeout=1.5))

        def drop_sigint(pgid, sig):
            if sig != signal.SIGINT:
                real_killpg(pgid, sig)

        with mock.patch.object(council_commit, "commit_owned", bounded), \
                mock.patch.object(council_commit, "_GRACE_SECONDS", 0.5), \
                mock.patch.object(council_commit.os, "killpg", drop_sigint):
            rc, docs, err = self.recover()
        rep = self.report(docs)
        rel = self.env.rel(self.env.council / name)
        self.assertEqual((rep["recovery"], rep["code"], rc), ("commit-refused", "timeout", 1), err)
        self.assertTrue((self.root / ".git" / "index.lock").exists(), "the positive control: the lock was left")
        self.assertEqual((rep.get("index_moved"), rep.get("staged"), rep.get("index_lock_left")),
                         (True, [rel], True), rep)
        self.assertEqual(rep.get("worktree_moved"), [rel], rep)
        self.assertNotIn("as it was", err)
        self.assertNotIn("as they were", err)
        self.assertIn("index.lock", err)
        self.assertEqual(self.head(), head0)
        self.assertIn(rel.encode(), self.index())
        self.assertEqual((self.env.council / name).read_bytes(), data, "the staged record's file is kept")
        self.assertEqual(self.pending(), {name: data})

        (self.root / ".git" / "index.lock").unlink()
        (self.root / ".git" / "hooks" / "pre-commit").unlink()
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        self.assertEqual(self.head_blob(rel), data)
        self.assertEqual(self.state(att).state, "resolved")

    def test_a_landed_commit_that_timed_out_in_verification_is_not_blamed_on_a_hook(self):
        """The commit lands and the call then runs out of time. The remedy is not to fix a hook.
        Positive control: HEAD moved and the report carries `commit_landed`. Mutation: one fixed
        remedy for every code -> 'fix the hook' for a timeout."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        real = council_commit.commit_owned

        def lands_then_times_out(*a, **k):
            real(*a, **k)
            raise council_commit.CommitRefused("timeout", "the call exceeded its bound")

        head0 = self.head()
        with mock.patch.object(council_commit, "commit_owned", lands_then_times_out):
            rc, docs, err = self.recover()
        rep = self.report(docs)
        self.assertEqual((rep["recovery"], rep["code"], rep.get("commit_landed"), rc),
                         ("commit-refused", "timeout", True, 1), err)
        self.assertNotEqual(self.head(), head0)
        self.assertNotIn("fix the hook", err)
        self.assertIn("run recovery again", err)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("recognised", 0), err)

    def test_an_environment_fault_after_an_earlier_act_names_the_earlier_act(self):
        """Round 1's pending copy commits; then reading the second item raises. The exit-2 line
        must name the earlier commit. Positive control: round 1's record is in HEAD. Mutation: let
        the fault reach `main` -> fixed text that does not name the commit."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        preflight = self.rec_name(round_=2).replace("120000", "140000")
        self.pend(preflight, cr.canonical_bytes(cr.sealed(self.env.preflight_doc("preflight-needs-owner",
                                                                                  round=2))))
        rel = self.env.rel(self.env.council / self.rec_name())
        for fault in (rec.Fatal("git cannot read the index"),
                      cr.RecordError("x.json", "the record cannot be read")):
            with self.subTest(type(fault).__name__):
                real = rec._act
                calls = []

                def act(ctx, item, fault=fault):
                    calls.append(item.record)
                    if len(calls) > 1:
                        raise fault
                    return real(ctx, item)

                head0 = self.head()
                try:
                    with mock.patch.object(rec, "_act", act):
                        rc, docs, err = self.recover()
                    self.assertEqual(rc, 2, err)
                    self.assertEqual(self.head_blob(rel), data)
                    self.assertIn("committed " + rel, err)
                    self.assertNotIn("nothing was changed", err)
                finally:
                    # Undo round 1's act for the next subtest: HEAD and the pending copy come back.
                    sup.git(self.root, "reset", "-q", "--hard", head0)
                    if self.rec_name() not in self.pending():
                        self.pend(self.rec_name(), data)

    def test_a_refusal_after_an_earlier_act_names_the_earlier_act(self):
        """Round 1's pending copy commits; then a hook rejects the commit of a preflight
        could-not-run record's pending copy. Recovery must report both items in order, and stderr must name the earlier
        commit rather than say nothing changed. Positive control: round 1's record is in HEAD.
        Mutation: report the refusal alone as 'nothing was committed'."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        preflight = self.rec_name(round_=2).replace("120000", "140000")
        self.pend(preflight, cr.canonical_bytes(cr.sealed(self.env.preflight_doc("preflight-needs-owner",
                                                                                  round=2))))
        self.install_hook("#!/bin/sh\ngit diff --cached --name-only | grep -q -- '-r2-' && "
                          "{ echo 'hook: refusing round 2' >&2; exit 1; }\nexit 0\n")
        rc, docs, err = self.recover()
        self.assertEqual(rc, 1, err)
        rel = self.env.rel(self.env.council / self.rec_name())
        tokens = [d["recovery"] for d in docs if "recovery" in d]
        self.assertEqual(tokens, ["committed", "commit-refused"], docs)
        self.assertEqual(self.head_blob(rel), data)
        self.assertIn("committed " + rel, err)
        self.assertNotIn("nothing was committed", err)
        self.assertEqual(list(self.pending()), [preflight])


# ───────────────────────────── environment faults ─────────────────────────────


class EnvironmentFaultTests(_Base):

    def test_a_malformed_crux_env_file_exits_2_and_changes_nothing(self):
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        (self.crux_home / "env").write_text(" BAD LINE=" + KEY_SHAPED + "\n")
        crux_env._reset_cache()
        before = self.frozen()
        rc, docs, err = self.recover()
        self.assertEqual(rc, 2, err)
        self.assertEqual(docs, [])
        self.assertIn("crux env file", err)
        self.assertEqual(self.frozen(), before)

    def test_no_fcntl_exits_2_before_any_write(self):
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        before = self.frozen()
        with mock.patch.object(rec, "fcntl", None):
            rc, docs, err = self.recover()
        self.assertEqual((rc, docs), (2, []), err)
        self.assertIn("fcntl", err)
        self.assertEqual(self.frozen(), before)

    def test_an_attempt_lock_that_cannot_be_probed_refuses_and_changes_nothing(self):
        """Opening the attempt record for the lock probe fails with a permission error. Only a
        missing file reads free; any other failure refuses before any write. Mutation: treat every
        OSError as free -> recovery commits under a lock it never probed."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        before = self.frozen()
        real_open = os.open
        probe_flags = os.O_RDONLY | os.O_NOFOLLOW

        def refuse(path, flags, *a, **k):
            if str(path).endswith(".attempt.json") and flags == probe_flags:
                raise PermissionError(13, "Permission denied")
            return real_open(path, flags, *a, **k)

        with mock.patch.object(rec.os, "open", refuse):
            rc, docs, err = self.recover()
            prc, pdocs, perr = self.recover("--probe")
        self.assertEqual((rc, docs), (2, []), err)
        self.assertIn("cannot be probed", err)
        self.assertEqual((prc, pdocs[0]["lock"]), (1, "unknown"), perr)
        self.assertEqual(self.frozen(), before)
        rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)

    def test_an_unbindable_run_exits_2(self):
        rc = rec.main([str(self.tmp / "missing" / "run-RUN-001.yaml")])
        self.assertEqual(rc, 2)

    def test_a_missing_pyyaml_exits_2_with_a_capability_message(self):
        """Without PyYAML the program exits 2 with the capability message, never a traceback.

        Red when the guarded `import yaml` is a bare import: the ImportError escapes, Python prints
        a traceback and exits 1. The control runs the same harness with PyYAML importable and
        reaches argparse, so the blocked run's exit 2 is the guard's and not the harness's.
        """
        harness = ("import runpy, sys\n"
                   "if sys.argv[1] == 'block':\n"
                   "    sys.modules['yaml'] = None\n"
                   "sys.argv = [{path!r}, '--help']\n"
                   "runpy.run_path({path!r}, run_name='__main__')\n").format(path=str(RECOVERY))

        def run(mode):
            return subprocess.run([sys.executable, "-c", harness, mode], cwd=self.root, env=sup.scrubbed_env(),
                                  capture_output=True, text=True, timeout=120)

        control = run("allow")
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertIn("run-RUN-NNN.yaml", control.stdout)
        blocked = run("block")
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("requires PyYAML", blocked.stderr)
        self.assertNotIn("Traceback", blocked.stderr)
        self.assertEqual(blocked.stdout, "")


# ───────────────────────────── no gateway, no key ─────────────────────────────

_CHILD = textwrap.dedent('''
    import json, socket, sys
    sys.path.insert(0, {scripts!r})
    import crux_env

    def refuse(*a, **k):
        raise AssertionError("recovery called a key accessor")

    crux_env.get = crux_env.get_optional = crux_env.require = refuse

    def no_network(*a, **k):
        raise AssertionError("recovery opened a network connection")

    socket.socket.connect = no_network
    socket.create_connection = no_network
    import council_recovery
    rc = council_recovery.main(sys.argv[1:])
    loaded = sorted(m for m in sys.modules if m == "crux.council" or m.startswith("crux.council.")
                    or m == "crux.core.llm_caller")
    print(json.dumps({{"rc": rc, "loaded": loaded}}))
''')


class NoGatewayTests(_Base):
    """The recovery module alone: imported by itself, it loads no council or gateway module, reads no
    key, opens no socket and starts no git child with a key. `run-council.py --recover` imports
    those modules through its own imports and never calls them; that route has its own cases in
    `test_council_recovery_failclosed.py`."""

    def child_env(self) -> dict:
        env = sup.scrubbed_env()
        env.pop("OPENROUTER_API_KEY", None)
        return env

    def test_recovery_loads_no_council_or_gateway_module_and_reads_no_key(self):
        """Run in a child with no key, key accessors replaced by failures and sockets refused.
        Positive control: the child commits, so recovery really ran its commit path.
        Mutation: import crux.council or call crux_env.get -> the child reports it or fails."""
        att = self.attempt()
        data = self.record_bytes(att)
        self.pend(self.rec_name(), data)
        script = _CHILD.format(scripts=str(SCRIPTS))
        proc = subprocess.run([sys.executable, "-c", script, str(self.env.run_path), "--prompt", str(PROMPT)],
                              cwd=self.root, env=self.child_env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        docs = json_docs(proc.stdout)
        self.assertEqual(docs[0]["recovery"], "committed", proc.stdout + proc.stderr)
        self.assertEqual(docs[-1], {"rc": 0, "loaded": []})
        self.assertEqual(self.head_blob(self.env.rel(self.env.council / self.rec_name())), data)

    def test_no_git_child_inherits_the_key_or_an_env_file_name(self):
        """The key is exported and the crux env file names another secret. Every child process
        recovery starts, the shared read helpers' git children included, gets neither. Positive
        control: the child commits, so its git children ran, and the probe saw them.
        Mutation: build any child's environment from `os.environ` unscrubbed -> the key reaches it."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        (self.crux_home / "env").write_text("CRUX_RECOVERY_EXTRA=zz-extra-dummy-value-9a7e\n")
        probe = textwrap.dedent('''
            import json, os, subprocess, sys
            sys.path.insert(0, {scripts!r})
            seen = []
            real = subprocess.Popen.__init__

            def spy(self, args, *a, **k):
                env = k.get("env")
                env = dict(os.environ) if env is None else dict(env)
                seen.append(sorted(n for n in ("OPENROUTER_API_KEY", "CRUX_RECOVERY_EXTRA") if n in env)
                            + sorted(n for n, v in env.items() if v in ("zz-gateway-dummy-9a7e",
                                                                        "zz-extra-dummy-value-9a7e")))
                return real(self, args, *a, **k)

            subprocess.Popen.__init__ = spy
            import council_recovery
            rc = council_recovery.main(sys.argv[1:])
            print(json.dumps({{"rc": rc, "children": len(seen), "leaks": [s for s in seen if s]}}))
        ''').format(scripts=str(SCRIPTS))
        env = self.child_env()
        env["OPENROUTER_API_KEY"] = "zz-gateway-dummy-9a7e"
        proc = subprocess.run([sys.executable, "-c", probe, str(self.env.run_path), "--prompt", str(PROMPT)],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=120)
        self.assertNotIn("zz-gateway-dummy-9a7e", proc.stdout + proc.stderr)
        docs = json_docs(proc.stdout)
        self.assertEqual(docs[0]["recovery"], "committed", proc.stdout + proc.stderr)
        self.assertEqual((docs[-1]["rc"], docs[-1]["leaks"]), (0, []))
        self.assertGreater(docs[-1]["children"], 5)

    def test_recovery_deletes_the_key_names_from_its_own_environment_before_any_read(self):
        """The second guard: every shared read helper drops the key names itself, and recovery
        also deletes them from its own environment before the first read, so a helper that did
        not drop them would still start git without them. Positive control: both names are in
        the environment when recovery starts. Mutation: skip the deletion -> the names are still
        there when the attempt states are read."""
        att = self.attempt()
        self.pend(self.rec_name(), self.record_bytes(att))
        (self.crux_home / "env").write_text("CRUX_RECOVERY_EXTRA=zz-extra-dummy-value-9a7e\n")
        crux_env._reset_cache()
        os.environ["OPENROUTER_API_KEY"] = "zz-gateway-dummy-9a7e"
        os.environ["CRUX_RECOVERY_EXTRA"] = "zz-extra-dummy-value-9a7e"
        seen: list = []
        real = cg.attempt_states

        def spy(*a, **k):
            seen.append(sorted(n for n in ("OPENROUTER_API_KEY", "CRUX_RECOVERY_EXTRA") if n in os.environ))
            return real(*a, **k)

        with mock.patch.object(rec.cg, "attempt_states", spy):
            rc, docs, err = self.recover()
        self.assertEqual((self.report(docs)["recovery"], rc), ("committed", 0), err)
        self.assertTrue(seen, "the attempt states were never read")
        self.assertEqual(seen, [[]] * len(seen))

    def test_the_script_runs_as_a_program(self):
        """`council_recovery.py` has a `__main__` that returns `main`'s code."""
        self.attempt()
        proc = subprocess.run([sys.executable, str(RECOVERY), str(self.env.run_path), "--prompt", str(PROMPT)],
                              cwd=self.root, env=self.child_env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json_docs(proc.stdout)[0]["recovery"], "unproven")


if __name__ == "__main__":
    unittest.main()
