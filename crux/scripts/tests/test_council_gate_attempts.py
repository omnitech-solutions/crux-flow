"""The council gate reads committed council attempts.

Each rule below is shown with a positive control and the named code mutation that turns its
assertion red:

* An open committed attempt in scope stops the gate at 4 before any pass or route, its attempt
  record decides, and the record must be attached. Mutation: drop the open-attempt check in
  `evaluate_council_gate` -> the replacement round passes.
* An uncommitted attempt or format-2 council record refuses and names recovery, never "commit the
  record"; a refutation or owner-exception record keeps "commit the record before the advance".
  Mutation: one message for every record type.
* A committed format-2 record whose seal fails, or whose bytes differ from its pending copy, is no
  evidence and takes no place: its attempt stays open (stop 4), and `expected_round` counts it the
  way the gate does. Mutation: count every `ran` record in `_walk_places`.
* A format-2 record naming a missing attempt is refused; a format-2 `ran` record naming none is
  refused. Mutation: skip the binding check.
* A format-1 council record is refused in a run whose base_commit cannot be shown to predate format
  version 2. Mutation: drop the `v1_admissible` call.
* A deciding `preflight-retries-spent` record stops at 1; `preflight-needs-owner` stops at 4;
  neither takes a place. Mutation: map every could-not-run code to 4.
* A void-attempt owner exception closes its attempt only when no council record or pending copy
  names it, and a voided attempt takes no place; `authorized()` skips a void. Mutation: drop the
  void refusal -> the void closes an attempt whose result survives; drop the skip -> KeyError.

Records are built with the `_council_gate_support` builders; the gate is driven through
`evaluate_council_gate` and through `advance-run.py`'s command line. Temporary repositories carry
an isolated git configuration. Reads only `crux/` and committed fixtures.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
from _council_gate_support import ts  # noqa: E402

import council_gate as cg  # noqa: E402
import council_records as cr  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
ADVANCE = SCRIPTS / "advance-run.py"
COUNCIL_N = 3
RC, OK = "REQUEST_CHANGES", "APPROVE"
BLOCK = {"openai_top": [("F1", "Correctness", False, "blocking")]}
RECOVER = "run-council.py --recover"
COMMIT_IT = "commit the record before the advance"


class _Base(unittest.TestCase):
    BASE = True

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name).resolve()
        self.git_env = sup.isolated_git_config(tmp / "cfg")
        patcher = mock.patch.dict(os.environ, self.git_env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = sup.Env(tmp / "repo", base=self.BASE, autocommit=True)
        self.env.set_run(COUNCIL_N)

    # -- builders ------------------------------------------------------------

    def attempt(self, round=1, ordinal=1, commit=True) -> Path:
        name = self.env.attempt_name(prompt=COUNCIL_N, round=round, ordinal=ordinal)
        doc = self.env.attempt_doc(prompt=COUNCIL_N, round=round, ordinal=ordinal)
        return self.env.write_sealed(name, doc, commit=commit)

    def record(self, attempt: Path, name: str | None = None, commit=True, mutate=None, **kw) -> Path:
        doc = self.env.council_v2_doc(attempt, **kw)
        if mutate:
            mutate(doc)
        name = name or attempt.name.replace(".attempt.json", ".json")
        return self.env.write_sealed(name, doc, commit=commit)

    def v1(self, name="v1-r1.json", **kw) -> Path:
        kw.setdefault("module_tag", "adr-1")
        kw.setdefault("prompt", COUNCIL_N)
        return self.env.write(name, self.env.council_doc(**kw))

    def void(self, attempt: Path, name="void.json", commit=True) -> Path:
        return self.env.write(name, self.env.void_doc(attempt), commit=commit)

    def pending(self, name: str, data: bytes) -> Path:
        d = cr.pending_dir(self.env.root, sup.BOOK_ID, sup.RUN_ID)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        return d / name

    # -- drivers -------------------------------------------------------------

    def gate(self, *artifacts, outcome="done", start=None):
        env = self.env
        return cg.evaluate_gate(cg.classify(env.book, COUNCIL_N), env.load_run(), env.run_path, env.root,
                                env.args_for(*artifacts), outcome, start=start)

    def expected(self):
        return cg.expected_round(self.env.run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N)

    def cli(self, *args):
        child = sup.scrubbed_env()
        child.update(self.git_env)
        return subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), *args],
                              capture_output=True, text=True, cwd=self.env.root, env=child)

    def out(self, proc) -> dict:
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "empty stdout reads as a crash: " + proc.stderr)
        return json.loads(proc.stdout)

    def arts(self, *paths) -> str:
        return ",".join(self.env.rel(p) for p in paths)

    def prompt_state(self):
        run = self.env.load_run()
        return run["prompts"][COUNCIL_N - 1]["state"], run["current_prompt"]


class OpenAttemptStopTests(_Base):
    def test_an_open_attempt_stops_at_four_and_a_replacement_round_cannot_pass_over_it(self):
        lost = self.attempt(ordinal=1)  # its result never reached a commit
        replacement = self.attempt(ordinal=2)
        r = self.record(replacement)
        v = self.gate(r, lost)
        self.assertEqual(v.verdict, "stop", v.reasons)
        self.assertEqual(v.stops, [4])
        self.assertEqual(v.deciding_record, self.env.rel(lost))
        self.assertIn("open", v.reasons[0])
        # Positive control: the owner voids the lost attempt, and the replacement then decides.
        vd = self.void(lost)
        v = self.gate(r, vd)
        self.assertEqual(v.verdict, "pass", v.reasons)
        self.assertEqual(v.deciding_record, self.env.rel(r))

    def test_an_open_attempt_with_no_council_record_stops_instead_of_refusing(self):
        a = self.attempt()
        v = self.gate(a)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(a)), v.reasons)
        # Positive control: its own record resolves it and decides the gate.
        r = self.record(a)
        self.assertEqual(self.gate(r).verdict, "pass")

    def test_the_open_attempt_must_be_attached(self):
        a = self.attempt()
        v = self.gate()
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(a), v.reasons[0])
        self.assertIn("not among the attached artifacts", v.reasons[0])
        self.assertEqual(self.gate(a).verdict, "stop", "control: attached, it stops")

    def test_the_open_attempt_stop_comes_before_a_held_round_and_a_route(self):
        a1 = self.attempt(round=1)
        r1 = self.record(a1, decisions=(RC, OK, OK), findings=BLOCK)
        self.assertEqual(self.gate(r1).verdict, "route", "control: the resolved round routes")
        a2 = self.attempt(round=2)
        v = self.gate(r1, a2)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(a2)), v.reasons)

    def test_cli_blocked_with_the_attempt_writes_blocked_and_done_writes_nothing(self):
        a = self.attempt()
        before = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "done", "--artifacts", self.arts(a))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = self.out(proc)
        self.assertEqual(payload["gate"]["verdict"], "stop")
        self.assertEqual(payload["gate"]["stops"], [4])
        self.assertEqual(self.env.run_path.read_bytes(), before, "a refused done writes nothing")
        proc = self.cli("--outcome", "blocked", "--result", "open attempt", "--artifacts", self.arts(a))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = self.out(proc)
        self.assertEqual(payload["gate"]["stops"], [4])
        self.assertEqual(payload["gate"]["deciding_record"], self.env.rel(a))
        self.assertEqual(self.prompt_state(), ("blocked", COUNCIL_N))

    def test_cli_blocked_without_the_attempt_attached_is_refused(self):
        a = self.attempt()
        r_other = self.v1("v1-unrelated.json")
        before = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "blocked", "--artifacts", self.arts(r_other))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(self.env.rel(a), self.out(proc)["error"])
        self.assertEqual(self.env.run_path.read_bytes(), before)


class UncommittedAndRemovedTests(_Base):
    def test_an_uncommitted_attempt_refuses_and_names_recovery(self):
        a = self.attempt(commit=False)
        v = self.gate(a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(a), v.reasons[0])
        self.assertIn(RECOVER, v.reasons[0])
        self.assertNotIn("commit the record", v.reasons[0])
        self.env.commit_records(a)
        self.assertEqual(self.gate(a).verdict, "stop", "control: committed, it is an open attempt")

    def test_an_uncommitted_format_two_record_refuses_and_names_recovery(self):
        a = self.attempt()
        r = self.record(a, commit=False)
        v = self.gate(r)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(r), v.reasons[0])
        self.assertIn(RECOVER, v.reasons[0])
        self.assertNotIn("commit the record", v.reasons[0])
        self.env.commit_records(r)
        self.assertEqual(self.gate(r).verdict, "pass", "control: committed, it decides")

    def test_the_recovery_command_names_the_run_and_the_prompt(self):
        """Recovery without `--prompt` cannot recognise a record whose pending copy is gone, so the
        command each refusal names carries the run snapshot and the prompt. Red when the uncommitted
        refusal or the void refusal names a bare `run-council.py --recover`. Positive control: the
        open-attempt stop already names the full command."""
        full = f"{RECOVER} {shlex.quote(self.env.rel(self.env.run_path))} --prompt {COUNCIL_N}"
        a = self.attempt(commit=False)
        self.assertIn(full, self.gate(a).reasons[0])
        self.env.commit_records(a)
        self.assertIn(full, " ".join(self.gate(a).reasons))  # the control
        doc = cr.sealed(self.env.council_v2_doc(a))
        self.pending("lost.json", cr.canonical_bytes(doc))
        vd = self.void(a)
        v = self.gate(vd, a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(full, v.reasons[0])

    def test_an_uncommitted_owner_exception_or_refutation_keeps_the_commit_message(self):
        r1 = self.v1("v1-r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        owner = self.env.write("owner.json", self.env.owner_doc(), commit=False)
        v = self.gate(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(owner), v.reasons[0])
        self.assertIn(COMMIT_IT, v.reasons[0])
        self.assertNotIn(RECOVER, v.reasons[0])
        self.env.commit_records(owner)
        ref = self.env.write("ref.json", self.env.refutation_doc(r1, written_at=ts(2)), commit=False)
        v = self.gate(ref)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(ref), v.reasons[0])
        self.assertIn(COMMIT_IT, v.reasons[0])
        self.env.commit_records(ref)
        self.assertEqual(self.gate(ref).verdict, "pass", "control: committed, the refutation decides")

    def test_a_committed_attempt_deleted_from_the_working_tree_refuses(self):
        a = self.attempt()
        data = a.read_bytes()
        a.unlink()
        v = self.gate()
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("never cleared by deleting", v.reasons[0])
        a.write_bytes(data)
        self.assertEqual(self.gate(a).verdict, "stop", "control: restored, it is open again")


class DivergedCommittedRecordTests(_Base):
    """A format-2 council record HEAD holds, naming a committed attempt, whose working-tree copy no
    longer matches HEAD (a hook altered it between the commit and verification, or it was edited or
    deleted) resolves nothing: the attempt stays open and the gate stops at 4, which the conductor
    can advance blocked. Mutation: drop the committed-attempt exemption from `_uncommitted_problem`
    and `_removed_record_problem` -> the gate refuses and the prompt cannot be advanced blocked.
    Controls: a format-2 record HEAD never held still refuses (UncommittedAndRemovedTests), and a
    committed format-1 record edited in the working tree still refuses."""

    def test_an_edited_committed_record_leaves_its_attempt_open_at_stop_four(self):
        a = self.attempt()
        r = self.record(a)
        self.assertEqual(self.gate(r).verdict, "pass", "control: the sealed record decides")
        r.write_text(json.dumps(json.loads(r.read_bytes()), indent=4) + "\n")
        v = self.gate(r, a)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(a)), v.reasons)
        proc = self.cli("--outcome", "blocked", "--artifacts", self.arts(a))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.prompt_state(), ("blocked", COUNCIL_N))

    def test_a_deleted_committed_record_refuses_and_no_void_or_replacement_passes_over_it(self):
        """A committed held record deleted from the working tree is never cleared by the deletion:
        the gate refuses, a void naming its attempt is refused while HEAD still holds the record,
        and a replacement round at the same number never passes. Mutation: exempt a deleted record
        too, or count only working-tree records as void survivors -> the void closes the attempt
        and the replacement round passes."""
        a = self.attempt()
        r = self.record(a, decisions=(RC, OK, OK))
        self.assertEqual((self.gate(r).verdict, self.gate(r).stops), ("stop", [1]), "control: a held round")
        r.unlink()
        v = self.gate(a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("never cleared by deleting", " ".join(v.reasons))
        void = self.void(a)
        a2 = self.attempt(ordinal=2)
        r2 = self.record(a2, written_at=ts(5))
        v = self.gate(r2, a, void)
        self.assertNotEqual(v.verdict, "pass", v.reasons)
        self.env.root.joinpath(self.env.rel(void)).unlink()
        sup.git(self.env.root, "rm", "-q", "--cached", "--", self.env.rel(void))
        sup.git(self.env.root, "commit", "-q", "-m", "drop void", "--", self.env.rel(void))
        sup.git(self.env.root, "checkout", "--", self.env.rel(r))
        v = self.gate(r, r2)
        self.assertEqual(v.verdict, "stop", v.reasons)

    def test_a_void_is_refused_while_head_holds_a_record_naming_the_attempt(self):
        """Second guard behind the deletion refusal: the void check counts a record HEAD holds as a
        survivor even when the working tree lacks it. Mutation: drop `head_naming` from
        `_void_problems` -> no refusal."""
        a = self.attempt()
        r = self.record(a)
        r.unlink()
        void = self.void(a)
        run = self.env.load_run()
        records = cr.discover_records(self.env.run_dir)
        states = cg.attempt_states(run, self.env.run_dir, self.env.root, "adr-1", COUNCIL_N, records=records)
        owners = [x for x in records if x.record_type == "owner-exception"]
        self.assertEqual([st.naming for st in states], [[]], "control: no working-tree record names it")
        self.assertIsNone(cg._void_problems(self.env.root, owners, states, run, "adr-1", COUNCIL_N),
                          "control: without HEAD's records the void is admitted")
        head = cg._head_records_naming(self.env.root, run, self.env.council)
        why = cg._void_problems(self.env.root, owners, states, run, "adr-1", COUNCIL_N, head)
        self.assertIn("in HEAD", why or "")
        self.assertIn(self.env.rel(void), why or "")

    def test_control_an_edited_committed_format_one_record_still_refuses(self):
        r1 = self.v1("v1-r1.json", written_at=ts(1))
        self.env.commit_records(r1)
        r1.write_text(r1.read_text().replace('"round": 1', '"round": 1 '))
        v = self.gate(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("is not committed", " ".join(v.reasons))


class EvidenceMatchesItsAttemptTests(_Base):
    def test_a_reformatted_committed_record_is_no_evidence_and_its_attempt_stops_at_four(self):
        a = self.attempt()
        r = self.record(a)
        self.assertEqual(self.gate(r).verdict, "pass", "control: the sealed record decides")
        doc = json.loads(r.read_bytes())
        r.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n")
        self.env.commit_records(r)
        v = self.gate(r, a)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(a)), v.reasons)
        self.assertIn("seal", " ".join(v.reasons))

    def test_a_record_that_differs_from_its_pending_copy_is_no_evidence(self):
        a = self.attempt()
        r = self.record(a)
        self.pending(r.name, r.read_bytes())
        self.assertEqual(self.gate(r).verdict, "pass", "control: an equal pending copy")
        self.pending(r.name, r.read_bytes().replace(b'"round": 1', b'"round": 1 '))
        v = self.gate(r, a)
        self.assertEqual((v.verdict, v.stops), ("stop", [4]), v.reasons)
        self.assertIn("pending copy", " ".join(v.reasons))

    def test_a_record_that_is_no_evidence_takes_no_place_for_the_gate_or_the_runner(self):
        a1 = self.attempt(round=1)
        r1 = self.record(a1, written_at=ts(1))
        self.assertEqual(self.expected(), 2, "control: the resolving record takes place 1")
        # A second copy naming the same attempt, reformatted by a hook: its seal fails, so it is no
        # evidence. Counted, it would take place 2, carry round 1 and decide (a misnumbered stop 1).
        stale = self.env.council / "stale-copy.json"
        doc = json.loads(r1.read_bytes())
        doc["written_at"] = ts(2)
        stale.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n")
        self.env.commit_records(stale)
        self.assertEqual(self.expected(), 2, "a record whose seal fails takes no place")
        v = self.gate(r1)
        self.assertEqual((v.verdict, v.deciding_record), ("pass", self.env.rel(r1)), v.reasons)
        self.assertIn(f"not evidence, and takes no place: {self.env.rel(stale)}", " ".join(v.reasons))

    def test_a_format_two_record_naming_a_missing_attempt_is_refused(self):
        a = self.attempt()
        r = self.record(a)
        self.assertEqual(self.gate(r).verdict, "pass", "control")
        ghost = self.env.council / self.env.attempt_name(prompt=COUNCIL_N, round=1, ordinal=9)
        r2 = self.record(a, name="ghost.json", written_at=ts(4),
                         mutate=lambda d: d["attempt"].update(path=self.env.rel(ghost)))
        v = self.gate(r2)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("names an attempt record", v.reasons[0])
        self.assertIn(self.env.rel(r2), v.reasons[0])

    def test_a_format_two_ran_record_naming_no_attempt_is_refused(self):
        a = self.attempt()
        r = self.record(a, mutate=lambda d: d.update(attempt=None))
        v = self.gate(r)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("#/attempt", " ".join(v.reasons), "the refusal is the schema's attempt rule")


class FormatOneAdmissibilityTests(_Base):
    def test_a_format_one_record_passes_in_a_run_whose_base_predates_format_two(self):
        r1 = self.v1()
        self.assertEqual(self.gate(r1).verdict, "pass")

    def test_a_format_one_record_is_refused_when_the_base_holds_an_attempt_record(self):
        r1 = self.v1()
        self.assertEqual(self.gate(r1).verdict, "pass", "control: the fixture base predates any attempt")
        a = self.attempt()
        head = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        start = cg.StartFields(known=True, base=head)
        self.void(a)
        v = self.gate(r1, start=start)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("format-1", v.reasons[0])
        self.assertIn("attempt record", v.reasons[0])

    def test_cli_threads_the_runs_history_pinned_base(self):
        r1 = self.v1()
        proc = self.cli("--outcome", "done", "--artifacts", self.arts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class FormatOneUnknownBaseTests(_Base):
    BASE = False

    def test_a_format_one_record_is_refused_when_the_run_records_no_base(self):
        r1 = self.v1()
        v = self.gate(r1)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("format-1", v.reasons[0])
        self.assertIn("base_commit", v.reasons[0])
        # Positive control: the same record with a known base passes.
        head = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        self.assertEqual(self.gate(r1, start=cg.StartFields(known=True, base=head)).verdict, "pass")

    def test_cli_refuses_a_format_one_record_with_no_base_and_writes_nothing(self):
        r1 = self.v1()
        before = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "done", "--artifacts", self.arts(r1))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("format-1", self.out(proc)["error"])
        self.assertEqual(self.env.run_path.read_bytes(), before)


class PreflightCodeTests(_Base):
    def preflight(self, code, name="preflight.json", **kw) -> Path:
        return self.env.write_sealed(name, self.env.preflight_doc(code, prompt=COUNCIL_N, **kw), commit=True)

    def test_retries_spent_stops_at_one_and_needs_owner_at_four(self):
        spent = self.preflight("preflight-retries-spent")
        v = self.gate(spent)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [1], self.env.rel(spent)), v.reasons)
        self.assertIn("preflight", v.reasons[0])
        owner = self.preflight("preflight-needs-owner", name="owner.json", written_at=ts(3))
        v = self.gate(owner)
        self.assertEqual((v.verdict, v.stops, v.deciding_record), ("stop", [4], self.env.rel(owner)), v.reasons)

    def test_neither_preflight_code_takes_a_place(self):
        self.preflight("preflight-retries-spent", written_at=ts(1, 0))
        self.preflight("preflight-needs-owner", name="owner.json", written_at=ts(1, 2))
        self.assertEqual(self.expected(), 1)
        a = self.attempt(round=1)
        r = self.record(a, written_at=ts(4))
        v = self.gate(r)
        self.assertEqual(v.verdict, "pass", v.reasons)

    def test_cli_blocked_with_a_retries_spent_record_writes_blocked_at_stop_one(self):
        spent = self.preflight("preflight-retries-spent")
        proc = self.cli("--outcome", "blocked", "--result", "retries spent", "--artifacts", self.arts(spent))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["stops"], [1])
        self.assertEqual(self.prompt_state(), ("blocked", COUNCIL_N))


class VoidAttemptTests(_Base):
    def test_a_void_is_refused_while_a_committed_record_names_the_attempt(self):
        a = self.attempt()
        r = self.record(a)
        doc = json.loads(r.read_bytes())
        r.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n")  # a hook altered it
        self.env.commit_records(r)
        vd = self.void(a)
        v = self.gate(r, vd, a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(vd), v.reasons[0])
        self.assertIn(RECOVER, v.reasons[0])

    def test_a_void_is_refused_while_a_pending_copy_names_the_attempt(self):
        a = self.attempt()
        doc = cr.sealed(self.env.council_v2_doc(a))
        self.pending("lost.json", cr.canonical_bytes(doc))
        vd = self.void(a)
        v = self.gate(vd, a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn("pending copy", v.reasons[0])
        self.assertIn(RECOVER, v.reasons[0])
        # Positive control: with no surviving copy the void closes the attempt.
        (cr.pending_dir(self.env.root, sup.BOOK_ID, sup.RUN_ID) / "lost.json").unlink()
        v = self.gate(vd, a)
        self.assertNotEqual(v.verdict, "stop", v.reasons)
        self.assertIn("no council record", v.reasons[0])

    def test_a_void_naming_no_attempt_in_scope_is_refused(self):
        a = self.attempt()
        doc = self.env.void_doc(a)
        doc["void_attempt"]["sha256"] = "0" * 64
        vd = self.env.write("void.json", doc, commit=True)
        v = self.gate(vd, a)
        self.assertEqual(v.verdict, "refuse", v.reasons)
        self.assertIn(self.env.rel(vd), v.reasons[0])

    def test_authorization_skips_a_void_exception(self):
        a = self.attempt(round=1)
        self.void(a, name="a-void.json")  # discovered before owner.json, so any() reaches it first
        self.v1("v1-r1.json", round=1, written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        self.v1("v1-r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        r3 = self.v1("v1-r3.json", round=3, written_at=ts(3))
        owner = self.env.write("owner.json", self.env.owner_doc(), commit=True)
        v = self.gate(r3, owner)
        self.assertEqual(v.verdict, "pass", v.reasons)



class GateInfoTextTests(_Base):
    def test_gate_info_names_the_open_attempt_stop_and_the_review_text_does_not(self):
        texts = {}
        for n in (COUNCIL_N, 5, 10):
            proc = self.cli("--gate-info", "--prompt", str(n))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            texts[n] = " ".join(self.out(proc)["requires"].split())
        for n in (COUNCIL_N, 5):
            self.assertIn("an open council attempt in scope", texts[n])
            self.assertIn("attach its attempt record with --outcome blocked", texts[n])
        self.assertNotIn("council attempt", texts[10], "control: the review gate reads no attempt")


if __name__ == "__main__":
    unittest.main()
