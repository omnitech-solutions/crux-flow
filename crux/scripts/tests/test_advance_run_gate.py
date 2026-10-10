"""The gate check at advance, through the real `advance-run.py` command line.

Each case builds a temp git repository holding a cycle book, a run snapshot at the real
layout and records, then runs the script as a subprocess (plus direct `main()` cases).
The book is resolved from the layout, never named with `--book`, unless a case says so.

Adr book: prompts 2-5 are `adr-1` (3 is the council prompt, 4 addresses findings, 5
closes), 10 is the independent-review prompt. Every refusal asserts the run snapshot's
bytes are unchanged and is paired with the passing control it was derived from.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _council_gate_support as sup  # noqa: E402
from _council_gate_support import ts  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
ADVANCE = SCRIPTS / "advance-run.py"
_spec = importlib.util.spec_from_file_location("advance_run_for_gate", ADVANCE)
_ar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ar)

RC, OK = "REQUEST_CHANGES", "APPROVE"
BLOCK = {"openai_top": [("F1", "Correctness", False, "blocking")]}
HISTORICAL = ("Convene the council on the proposal (use the council skill, or, equivalently, dispatch "
              "5 parallel review agents via the `Agent` tool).")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin_base(env, current, base=None):
    """Point the run at `current` and record `base_commit` (default HEAD), as run start does."""
    env.set_run(current)
    run = env.load_run()
    run["base_commit"] = base or sup.git(env.root, "rev-parse", "HEAD").strip()
    env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))


class _Case(unittest.TestCase):
    KIND = "adr"
    BOOK = None
    #: The fixture commit is the run's base_commit, so a format-1 record is admissible. A class whose
    #: tests build their own base history sets it false.
    BASE = True

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.env = sup.Env(Path(self._td.name) / "repo", kind=self.KIND, book=self.BOOK, autocommit=True,
                           base=self.BASE)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ADVANCE), str(self.env.run_path), *args],
                              capture_output=True, text=True, cwd=self.env.root, env=sup.scrubbed_env())

    def out(self, proc):
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "empty stdout reads as a crash: " + proc.stderr)
        return json.loads(proc.stdout)

    def council(self, name="r1.json", **kw):
        kw.setdefault("module_tag", None if self.KIND == "patch" else f"{self.KIND}-1")
        kw.setdefault("prompt", 1 if self.KIND == "patch" else 3)
        return self.env.write(name, self.env.council_doc(**kw))

    def artifacts(self, *paths):
        return ",".join(self.env.rel(p) for p in paths)

    def refused(self, proc, needle=None):
        """Exit 1, a JSON error, and the run snapshot untouched."""
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = self.out(proc)
        self.assertIn("error", payload)
        if needle:
            self.assertIn(needle, payload["error"] + json.dumps(payload.get("gate", {})))
        return payload

    def states(self):
        run = self.env.load_run()
        return {p["n"]: p["state"] for p in run["prompts"]}, run["current_prompt"], run["status"]


class CouncilGateEvidenceTests(_Case):
    def setUp(self):
        super().setUp()
        self.env.set_run(3)
        self.before = self.env.run_path.read_bytes()

    def assert_run_unchanged(self):
        self.assertEqual(self.env.run_path.read_bytes(), self.before)

    def test_a_runner_record_passes_and_advances(self):
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--result", "council converged", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual(out["current_prompt"], 4)
        self.assertEqual(out["gate"]["class"], "council")
        self.assertEqual(out["gate"]["verdict"], "pass")
        states, current, _ = self.states()
        self.assertEqual((states[3], states[4], current), ("done", "running", 4))

    def test_five_valid_reviewer_reports_cannot_satisfy_a_council_gate(self):
        reports = []
        for i in range(5):
            doc = self.env.report_doc(prompt=3, written_at=ts(30 + i))
            reports.append(self.env.write(f"rep{i}.json", doc, folder=self.env.run_dir / "reviews"))
        proc = self.cli("--outcome", "done", "--result", "five reviewers approved",
                        "--artifacts", self.artifacts(*reports))
        payload = self.refused(proc, "no council record")
        self.assertEqual(payload["gate"]["verdict"], "refuse")
        self.assert_run_unchanged()
        # Positive control: one runner record at the same gate passes.
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_only_a_runner_record_passes_a_council_gate(self):
        r1 = self.council()
        # (a) a result string claiming a council, with no record
        proc = self.cli("--outcome", "done", "--result", "council approved (5/5 reviewers)")
        self.refused(proc, "not among the attached artifacts")
        self.assert_run_unchanged()
        # (b) consensus JSON offered as evidence
        consensus = self.env.run_dir / "consensus.json"
        consensus.write_text(json.dumps({"consensus": "UNANIMOUS_APPROVE"}))
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(consensus))
        self.refused(proc, "not among the attached artifacts")
        self.assert_run_unchanged()
        # (c) a council record whose responding seat has no served model
        doc = self.env.council_doc(round=2, written_at=ts(2))
        doc["seats"][0]["served_model"] = None
        bad = self.env.write("r2.json", doc)
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1, bad))
        self.refused(proc, "served_model")
        self.assert_run_unchanged()
        self.env.write("r2.json", self.env.council_doc(round=2, written_at=ts(2)))
        # (d) a record whose writer is not the runner is invalid
        doc = self.env.council_doc(round=3, written_at=ts(3))
        doc["writer"] = "commander"
        self.env.write("r3.json", doc, commit=False)  # never committed, so deleting it is allowed
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.refused(proc, "council-record schema")
        self.assert_run_unchanged()
        (self.env.council / "r3.json").unlink()
        # Positive control: the committed runner records pass, whatever the result string says.
        r2 = self.env.council / "r2.json"
        proc = self.cli("--outcome", "done", "--result", "council approved (5/5 reviewers)",
                        "--artifacts", self.artifacts(r1, r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_result_text_is_never_read_for_a_gate_decision(self):
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)  # not converged
        proc = self.cli("--outcome", "done", "--result", "council converged, all approve",
                        "--artifacts", self.artifacts(r1))
        payload = self.refused(proc)
        self.assertEqual(payload["gate"]["verdict"], "route")
        self.assert_run_unchanged()

    def test_skipped_is_refused_at_a_gate(self):
        proc = self.cli("--outcome", "skipped", "--result", "n/a")
        self.refused(proc, "cannot be skipped")
        self.assert_run_unchanged()
        # Positive control: skipped is allowed at a prompt that is not a gate.
        self.env.set_run(1)
        proc = self.cli("--outcome", "skipped")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_blocked_needs_artifacts_at_a_gate(self):
        proc = self.cli("--outcome", "blocked", "--result", "stuck")
        self.refused(proc, "needs --artifacts")
        self.assert_run_unchanged()

    def test_blocked_with_a_refused_record_writes_nothing(self):
        proc = self.cli("--outcome", "blocked", "--artifacts", "docs/none.json")
        self.refused(proc, "refused the evidence")
        self.assert_run_unchanged()

    def test_blocked_with_a_passing_record_is_refused(self):
        r1 = self.council()
        proc = self.cli("--outcome", "blocked", "--artifacts", self.artifacts(r1))
        self.refused(proc, "the gate passes")
        self.assert_run_unchanged()

    def test_a_stop_writes_blocked_and_keeps_the_pointer_and_a_re_advance_is_allowed(self):
        held = self.council(decisions=(RC, OK, OK))
        proc = self.cli("--outcome", "blocked", "--result", "held round", "--artifacts", self.artifacts(held))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual((out["gate"]["verdict"], out["gate"]["stops"]), ("stop", [1]))
        states, current, status = self.states()
        self.assertEqual((states[3], states[4], current, status), ("blocked", "pending", 3, "in_progress"))
        run = self.env.load_run()
        self.assertEqual(run["prompts"][2]["result"], "held round")
        self.assertEqual(run["prompts"][2]["artifacts"], [self.env.rel(held)])
        # Deleting the held record and writing a converged round does not clear the stop (S3).
        held.unlink()
        r2 = self.council("r2.json", written_at=ts(2))
        stopped = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r2))
        self.refused(proc, self.env.rel(held))
        self.assertEqual(self.env.run_path.read_bytes(), stopped)
        self.assertEqual(self.states()[0][3], "blocked")
        # Positive control: with the held record restored, the same converged round at a later
        # place still meets the held stop, so the refusal above was the deletion.
        sup.git(self.env.root, "checkout", "--", self.env.rel(held))
        proc = self.cli("--outcome", "blocked", "--artifacts", self.artifacts(held, r2))
        out = self.out(proc)
        self.assertEqual((proc.returncode, out["gate"]["verdict"], out["gate"]["stops"]), (0, "stop", [1]))
        self.assertEqual(self.states()[0][3], "blocked")

    def test_disclosed_tampering_a_re_advance_naming_another_record_lets_a_committed_deletion_clear(self):
        """The committed-tampering case gates.md section 10 disclosed under the stamp order: a
        re-advance of the blocked prompt replaces its artifacts and keeps the old list only in the
        run's notes, which the gate does not read. Under the committed order a committed deletion
        of the held record is a permanent stop at 4, and gates.md section 10 no longer discloses
        the window."""
        held = self.council(decisions=(RC, OK, OK))
        proc = self.cli("--outcome", "blocked", "--result", "held round", "--artifacts", self.artifacts(held))
        self.assertEqual((proc.returncode, self.out(proc)["gate"]["stops"]), (0, [1]))
        r2 = self.council("r2.json", written_at=ts(2), round=1)
        sup.commit_all(self.env.root, "the stop and a second round")
        # Positive control: while the snapshot names the held record, its committed deletion refuses.
        sup.git(self.env.root, "rm", "-q", "--", self.env.rel(held))
        sup.git(self.env.root, "commit", "-q", "-m", "drop the held record")
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r2)), self.env.rel(held))
        sup.git(self.env.root, "revert", "--no-edit", "HEAD")
        # A tool-only re-advance with the second round attached: the held round still stops the gate.
        proc = self.cli("--outcome", "blocked", "--result", "again", "--artifacts", self.artifacts(r2))
        self.assertEqual((proc.returncode, self.out(proc)["gate"]["verdict"]), (0, "stop"), proc.stdout)
        run = self.env.load_run()
        self.assertEqual(run["prompts"][2]["artifacts"], [self.env.rel(r2)])
        self.assertIn(self.env.rel(held), run["notes"])
        sup.commit_all(self.env.root, "re-advanced")
        sup.git(self.env.root, "rm", "-q", "--", self.env.rel(held))
        sup.git(self.env.root, "commit", "-q", "-m", "drop the held record again")
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r2))
        gate = self.out(proc)["gate"]
        self.assertEqual((proc.returncode, gate["verdict"], gate["stops"]), (1, "stop", [4]), proc.stdout)
        self.assertIn(self.env.rel(held), " ".join(gate["reasons"]))

    def test_a_could_not_run_stop_clears_when_the_council_reconvenes_at_the_same_round(self):
        cnr = self.council("r0.json", outcome="could-not-run")
        proc = self.cli("--outcome", "blocked", "--result", "no key", "--artifacts", self.artifacts(cnr))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual((out["gate"]["verdict"], out["gate"]["stops"]), ("stop", [4]))
        self.assertEqual(self.states()[0][3], "blocked")
        # The cause is fixed; the council reconvenes at round 1 (a could-not-run record takes no place).
        r1 = self.council("r1.json", written_at=ts(2), round=1)
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(cnr, r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["verdict"], "pass")
        self.assertEqual(self.states()[0][3], "done")

    def test_a_route_from_ordinal_two_lands_on_ordinal_three(self):
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "findings", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual((out["gate"]["verdict"], out["gate"]["route_to"]), ("route", 4))
        states, current, status = self.states()
        self.assertEqual((states[3], states[4], states[5], current, status),
                         ("blocked", "running", "pending", 4, "in_progress"))
        run = self.env.load_run()
        self.assertIsNone(run["prompts"][3]["completed"])
        self.assertIsNotNone(run["prompts"][3]["started"])
        self.assertEqual(run["prompts"][2]["artifacts"], [self.env.rel(r1)])
        # Advancing ordinal 3 done moves to the first pending prompt: the close (5).
        proc = self.cli("--outcome", "done", "--result", "addressed")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.states()[1], 5)

    def test_a_route_from_ordinal_four_returns_to_ordinal_three_and_back(self):
        self.env.set_run(5)
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "close failed", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        states, current, _ = self.states()
        self.assertEqual((states[4], states[5], current), ("running", "pending", 4))
        run = self.env.load_run()
        self.assertIsNone(run["prompts"][3]["completed"])
        self.assertIsNone(run["prompts"][4]["completed"])
        # Advancing ordinal 3 done lands on ordinal 4 again, the first pending prompt.
        proc = self.cli("--outcome", "done", "--result", "addressed")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        states, current, _ = self.states()
        self.assertEqual((states[4], states[5], current), ("done", "running", 5))
        # The close now passes on a converged second round.
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1, r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.states()[1], 6)

    def test_a_route_is_refused_for_done_and_a_stop_is_refused_for_done(self):
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r1)), "does not pass (route)")
        held = self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK))
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r1, held)),
                     "does not pass (stop)")
        self.assert_run_unchanged()

    def test_a_deciding_could_not_run_record_stops_the_run_with_defer_to_human(self):
        cnr = self.council(outcome="could-not-run")
        proc = self.cli("--outcome", "blocked", "--result", "council could not run",
                        "--artifacts", self.artifacts(cnr))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual(out["gate"]["stops"], [4])
        self.assertEqual(self.states()[1:], (3, "in_progress"))


class ReviewGateEvidenceTests(_Case):
    def setUp(self):
        super().setUp()
        self.env.set_run(10)
        self.before = self.env.run_path.read_bytes()

    def report(self, name="rep.json", **kw):
        kw.setdefault("prompt", 10)
        return self.env.write(name, self.env.report_doc(**kw), folder=self.env.run_dir / "reviews")

    def test_a_council_record_cannot_satisfy_an_independent_review_gate(self):
        council = self.council()  # converged and valid
        proc = self.cli("--outcome", "done", "--result", "council approved",
                        "--artifacts", self.artifacts(council))
        payload = self.refused(proc, "never satisfies")
        self.assertEqual(payload["gate"]["class"], "independent-review")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        # Positive control: a valid reviewer report passes the same gate.
        rep = self.report()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(rep))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["current_prompt"], 11)

    def test_a_dirty_reviewed_path_is_refused_through_the_cli(self):
        rep = self.report()
        self.env.subject.write_text("# changed\n")
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(rep)), "unstaged change")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)

    def test_a_valid_report_with_outcome_blocked_advances_blocked_with_no_stop(self):
        rep = self.report()
        proc = self.cli("--outcome", "blocked", "--result", "review found issues",
                        "--artifacts", self.artifacts(rep))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        gate = self.out(proc)["gate"]
        self.assertEqual((gate["verdict"], gate["stops"]), ("pass", []))
        states, current, status = self.states()
        self.assertEqual((states[10], states[11], current, status), ("blocked", "running", 11, "in_progress"))
        run = self.env.load_run()
        self.assertEqual(run["prompts"][9]["result"], "review found issues")
        self.assertEqual(run["prompts"][9]["artifacts"], [self.env.rel(rep)])

    def test_blocked_without_a_valid_report_is_still_refused_at_the_review_gate(self):
        proc = self.cli("--outcome", "blocked", "--result", "x", "--artifacts", "docs/none.json")
        self.refused(proc, "does not exist")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        rep = self.report()
        self.env.subject.write_text("# changed\n")  # the reviewed path is no longer clean
        self.refused(self.cli("--outcome", "blocked", "--artifacts", self.artifacts(rep)), "unstaged change")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        council = self.council()
        self.refused(self.cli("--outcome", "blocked", "--artifacts", self.artifacts(council)), "never satisfies")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)

    def test_a_range_path_with_a_non_utf8_name_and_a_staged_change_is_refused(self):
        # A decode with "replace" turned the name into one with no tree entry, so the path was
        # read as deleted and the report passed with a staged change nobody reviewed (S5 MF-1).
        sup.commit_all(self.env.root, "at prompt 10")
        for name in (b"src/reviewed.txt", b"src/reviewed-\xff.txt"):  # the UTF-8 name is the control
            with self.subTest(name=name):
                if name != b"src/reviewed.txt":
                    sup.git(self.env.root, "reset", "-q", "--hard", "HEAD")
                rng = sup.range_with_a_staged_change(self.env.root, name)
                rep = self.report(f"rep-{len(name)}.json", range_=rng)
                payload = self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(rep)),
                                       "has a staged change")
                self.assertEqual(payload["gate"]["verdict"], "refuse")
                self.assertEqual(self.env.run_path.read_bytes(), self.before)

    def test_skipped_is_refused_at_the_review_gate(self):
        self.refused(self.cli("--outcome", "skipped"), "cannot be skipped")


BLOCK_TEXT = "  run notes line one\n\n    indented detail\n  last prior line\n"
BLOCK_VALUE = "run notes line one\n\n  indented detail\nlast prior line"


class RoutedRoundKeepsItsDataTests(_Case):
    """A route resets prompts; what the invocation carried must survive in the run's notes."""

    def set_notes(self, style):
        """Rewrite the run's `notes` as the given YAML scalar; return the old file text."""
        text = self.env.run_path.read_text()
        self.assertIn("notes: ''\n", text)
        if style in ("|", "|-", "|+"):
            text = text.replace("notes: ''\n", f"notes: {style}\n{BLOCK_TEXT}" + ("\n" if style == "|+" else ""))
        elif style == "single":
            text = text.replace("notes: ''\n", "notes: a single line of notes\n")
        self.env.run_path.write_text(text)
        return text

    def round_one(self):
        """Round 1 routed from ordinal 2 (prompt 3), then ordinal 3 (prompt 4) done."""
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "round-1 council: F1", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.cli("--outcome", "done", "--result", "ROUND-1 ADDRESS RESULT", "--artifacts", "docs/fix1.txt")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return r1

    def test_a_route_from_ordinal_four_keeps_the_result_artifacts_and_the_old_ordinal_three_data(self):
        self.env.set_run(3)
        r1 = self.round_one()
        self.assertEqual(self.env.load_run()["current_prompt"], 5)
        r2 = self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "ROUND-2 CLOSE RESULT",
                        "--artifacts", self.artifacts(r1, r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run = self.env.load_run()
        self.assertEqual(run["current_prompt"], 4)
        notes = run["notes"]
        for needle in ("ROUND-2 CLOSE RESULT", self.env.rel(r2), self.env.rel(r1), "ROUND-1 ADDRESS RESULT",
                       "docs/fix1.txt", "route", "deciding_record"):
            self.assertIn(needle, notes)
        # Ordinal 3 goes back to running with its previous result and artifacts cleared.
        el4 = run["prompts"][3]
        self.assertEqual((el4["state"], el4["result"], el4["artifacts"], el4["completed"]),
                         ("running", "", [], None))
        # Its next advance writes its own data; the notes keep the earlier rounds'.
        proc = self.cli("--outcome", "done", "--result", "round-2 address", "--artifacts", "docs/fix2.txt")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run = self.env.load_run()
        self.assertEqual((run["prompts"][3]["result"], run["prompts"][3]["artifacts"]),
                         ("round-2 address", ["docs/fix2.txt"]))
        self.assertIn("ROUND-1 ADDRESS RESULT", run["notes"])
        self.assertIn("ROUND-2 CLOSE RESULT", run["notes"])

    def test_a_route_from_ordinal_two_keeps_what_it_clears_from_ordinal_three(self):
        # The paired route: ordinal 2 writes its own data on its element and clears ordinal 3.
        self.env.set_run(3)
        run = self.env.load_run()
        run["prompts"][3]["result"] = "PLANTED OLD RESULT"
        run["prompts"][3]["artifacts"] = ["docs/old.txt"]
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "ORDINAL-2 RESULT", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run = self.env.load_run()
        self.assertEqual(run["prompts"][2]["result"], "ORDINAL-2 RESULT")
        self.assertEqual((run["prompts"][3]["result"], run["prompts"][3]["artifacts"]), ("", []))
        for needle in ("PLANTED OLD RESULT", "docs/old.txt", "ORDINAL-2 RESULT", self.env.rel(r1)):
            self.assertIn(needle, run["notes"])

    def test_a_block_scalar_note_stays_a_block_and_keeps_every_prior_byte(self):
        for style in ("|", "|-", "|+"):
            with self.subTest(style=style):
                self.env.set_run(3)
                old_text = self.set_notes(style)
                old_notes = self.env.load_run()["notes"]
                self.assertTrue(old_notes.startswith(BLOCK_VALUE))
                r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
                proc = self.cli("--outcome", "blocked", "--result", "NEW ROUTE", "--artifacts", self.artifacts(r1))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                new_text = self.env.run_path.read_text()
                self.assertIn(f"notes: {style}\n", new_text)
                self.assertNotIn('notes: "', new_text)
                cut = old_text.index("last prior line") + len("last prior line")
                # (The prompts above the notes change on a route; the notes block's old bytes do not.)
                at_old, at_new = old_text.index("\nnotes: "), new_text.index("\nnotes: ")
                self.assertEqual(new_text[at_new:at_new + cut - at_old], old_text[at_old:cut])
                tail = old_text[old_text.index("pr_draft:"):]
                self.assertTrue(new_text.endswith(tail), new_text[-200:])
                notes = self.env.load_run()["notes"]
                self.assertTrue(notes.startswith(old_notes.rstrip("\n")), notes)
                self.assertIn("NEW ROUTE", notes)
                if style != "|-":
                    self.assertTrue(notes.endswith("\n"))
                else:
                    self.assertFalse(notes.endswith("\n"))
                (self.env.council / "r1.json").unlink()

    def test_empty_notes_gain_a_literal_block_and_later_routes_append_to_it(self):
        self.env.set_run(3)
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "FIRST ROUTE", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("notes: |\n  ", self.env.run_path.read_text())
        self.assertIn("FIRST ROUTE", self.env.load_run()["notes"])
        proc = self.cli("--outcome", "done", "--result", "addressed")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        r2 = self.council("r2.json", round=2, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "SECOND ROUTE", "--artifacts", self.artifacts(r1, r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        notes = self.env.load_run()["notes"]
        self.assertIn("FIRST ROUTE", notes)
        self.assertIn("SECOND ROUTE", notes)
        self.assertIn("notes: |\n", self.env.run_path.read_text())

    def test_notes_that_cannot_take_the_entry_in_place_refuse_and_write_nothing(self):
        for name, notes_text in (("plain single line", "notes: a single line of notes\n"),
                                 ("plain multi-line", "notes: first line\n  second line\n"),
                                 ("folded", "notes: >\n  folded line\n"),
                                 ("double-quoted", 'notes: "quoted line"\n')):
            with self.subTest(notes=name):
                self.env.set_run(3)
                text = self.env.run_path.read_text().replace("notes: ''\n", notes_text)
                self.env.run_path.write_text(text)
                before = self.env.run_path.read_bytes()
                r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
                proc = self.cli("--outcome", "blocked", "--result", "ROUTE RESULT",
                                "--artifacts", self.artifacts(r1))
                self.refused(proc, "notes")
                self.assertEqual(self.env.run_path.read_bytes(), before)
                (self.env.council / "r1.json").unlink()
        # Positive control: the same route over a literal block appends in place.
        self.env.set_run(3)
        self.set_notes("|")
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "ROUTE RESULT", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ROUTE RESULT", self.env.load_run()["notes"])

    def test_hostile_result_text_cannot_break_the_notes_block(self):
        self.env.set_run(3)
        old_text = self.set_notes("|")
        nasty = "line\nnext: yes\r\n  # c\u2028\x85 \ttab \"quote\" ---\n...\n"
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", nasty, "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run = self.env.load_run()
        self.assertTrue(run["notes"].startswith(BLOCK_VALUE + "\n"))
        self.assertEqual((run["current_prompt"], run["pr_draft"], run["summary"]), (4, "", ""))
        self.assertEqual(self.env.run_path.read_text()[:60], old_text[:60])

    def test_a_failed_postcondition_writes_nothing(self):
        self.env.set_run(3)
        self.set_notes("|")
        before = self.env.run_path.read_bytes()
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        orig = _ar.load_yaml
        original_text = before.decode("utf-8")
        # Tamper only the postcondition's re-read (the spliced text), never the initial load.
        _ar.load_yaml = lambda text: orig(text) if text == original_text else {**orig(text), "notes": "tampered"}
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    _ar.main([str(self.env.run_path), "--outcome", "blocked", "--result", "x",
                              "--artifacts", self.artifacts(r1)])
        finally:
            _ar.load_yaml = orig
        self.assertEqual(self.env.run_path.read_bytes(), before)
        # Positive control: the same invocation without the tamper writes.
        proc = self.cli("--outcome", "blocked", "--result", "x", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotEqual(self.env.run_path.read_bytes(), before)


class ReAdvanceOfAStoppedGateKeepsItsDataTests(_Case):
    """A stop records the prompt `blocked` and keeps the pointer on it; a re-advance of that
    prompt overwrites its result and artifacts, so the overwritten data must reach the notes."""

    set_notes = RoutedRoundKeepsItsDataTests.set_notes

    def setUp(self):
        super().setUp()
        self.env.set_run(3)

    def stop(self, name, result, **kw):
        held = self.council(name, decisions=(RC, OK, OK), **kw)
        proc = self.cli("--outcome", "blocked", "--result", result, "--artifacts", self.artifacts(held))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["verdict"], "stop")
        return held

    def stop_could_not_run(self, name, result):
        """A stop that clears: the council could not run. Its record stays committed and takes no place."""
        cnr = self.council(name, outcome="could-not-run")
        proc = self.cli("--outcome", "blocked", "--result", result, "--artifacts", self.artifacts(cnr))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["stops"], [4])
        return cnr

    def test_a_stop_then_a_done_re_advance_keeps_the_stop_result_and_artifacts(self):
        self.stop_could_not_run("s1.json", "STOP-1 RESULT")
        run = self.env.load_run()
        self.assertEqual(run["notes"], "")  # a first advance of a running prompt adds no entry
        r2 = self.council("r2.json", written_at=ts(2))
        proc = self.cli("--outcome", "done", "--result", "NEW DONE RESULT", "--artifacts", self.artifacts(r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run = self.env.load_run()
        self.assertEqual(run["prompts"][2]["result"], "NEW DONE RESULT")
        self.assertEqual(run["prompts"][2]["artifacts"], [self.env.rel(r2)])
        for needle in ("STOP-1 RESULT", "s1.json", "prompt 3", "blocked", "done"):
            self.assertIn(needle, run["notes"])

    def test_a_stop_then_another_stop_keeps_both_results(self):
        self.stop("s1.json", "STOP-1 RESULT")
        self.stop("s2.json", "STOP-2 RESULT", round=2, written_at=ts(2))
        run = self.env.load_run()
        self.assertEqual(run["prompts"][2]["result"], "STOP-2 RESULT")
        self.assertIn("STOP-1 RESULT", run["notes"])
        self.assertIn("s1.json", run["notes"])
        self.assertEqual(run["notes"].count("previous result"), 1)
        # A third stop: the second stop's data reaches the notes, the first stays.
        self.stop("s3.json", "STOP-3 RESULT", round=3, written_at=ts(3))
        notes = self.env.load_run()["notes"]
        for needle in ("STOP-1 RESULT", "STOP-2 RESULT", "s2.json"):
            self.assertIn(needle, notes)
        self.assertEqual(notes.count("STOP-1 RESULT"), 1)

    def test_a_first_advance_of_a_running_prompt_leaves_the_notes_byte_identical(self):
        old_text = self.set_notes("|")
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--result", "first", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        new_text = self.env.run_path.read_text()
        self.assertEqual(new_text[new_text.index("\nnotes: "):][:len(BLOCK_TEXT) + 9],
                         old_text[old_text.index("\nnotes: "):][:len(BLOCK_TEXT) + 9])
        self.assertEqual(self.env.load_run()["notes"], BLOCK_VALUE + "\n")

    def test_a_block_scalar_note_keeps_its_prefix_bytes_when_the_entry_is_appended(self):
        old_text = self.set_notes("|")
        self.stop_could_not_run("s1.json", "STOP-1 RESULT")
        r2 = self.council("r2.json", written_at=ts(2))
        proc = self.cli("--outcome", "done", "--result", "NEW", "--artifacts", self.artifacts(r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        new_text = self.env.run_path.read_text()
        self.assertIn("notes: |\n", new_text)
        cut = old_text.index("last prior line") + len("last prior line")
        at_old, at_new = old_text.index("\nnotes: "), new_text.index("\nnotes: ")
        self.assertEqual(new_text[at_new:at_new + cut - at_old], old_text[at_old:cut])
        notes = self.env.load_run()["notes"]
        self.assertTrue(notes.startswith(BLOCK_VALUE))
        self.assertIn("STOP-1 RESULT", notes)

    def test_a_refused_re_advance_writes_no_entry(self):
        self.stop("s1.json", "STOP-1 RESULT")
        before = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "skipped")
        self.refused(proc, "cannot be skipped")
        self.assertEqual(self.env.run_path.read_bytes(), before)


class NotesLayoutEdgeTests(_Case):
    """The notes append keeps a literal block's layout at its edges: empty notes with two
    entries in one advance, a whitespace-only line past the block indent, and a block that is the
    last key of the file."""

    def set_run_text(self, notes_text, last=False):
        """Write `notes_text` (a full `notes:` entry) as the run's notes; `last` moves it to EOF."""
        self.env.set_run(3)
        text = self.env.run_path.read_text()
        self.assertIn("notes: ''\n", text)
        if last:
            text = text.replace("notes: ''\n", "")
            text = text.rstrip("\n") + "\n" + notes_text
        else:
            text = text.replace("notes: ''\n", notes_text)
        self.env.run_path.write_text(text)
        return text

    def route(self, name="r1.json", result="ROUTE", **kw):
        r = self.council(name, decisions=(RC, OK, OK), findings=BLOCK, **kw)
        proc = self.cli("--outcome", "blocked", "--result", result, "--artifacts", self.artifacts(r))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["verdict"], "route")
        return r

    def test_empty_notes_with_a_stop_then_a_route_in_two_advances_become_one_block(self):
        self.env.set_run(3)
        held = self.council("s1.json", outcome="could-not-run")
        proc = self.cli("--outcome", "blocked", "--result", "STOP-1", "--artifacts", self.artifacts(held))
        self.assertEqual(self.out(proc)["gate"]["verdict"], "stop")
        self.assertEqual(self.out(proc)["gate"]["stops"], [4])
        # The re-advance of the blocked prompt appends the kept evidence, then the route appends
        # its note, both in one invocation, from notes that began empty.
        r2 = self.council("r2.json", round=1, written_at=ts(2), decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "ROUTE-2", "--artifacts", self.artifacts(held, r2))
        self.assertEqual(self.out(proc)["gate"]["verdict"], "route", proc.stdout)
        text = self.env.run_path.read_text()
        self.assertIn("notes: |\n  ", text)
        self.assertNotIn('notes: "', text)
        notes = self.env.load_run()["notes"]
        self.assertIn("STOP-1", notes)
        self.assertIn("ROUTE-2", notes)
        self.assertIn("re-advance of blocked prompt", notes)
        # A later advance appends into the same block and keeps every earlier note byte.
        proc = self.cli("--outcome", "done", "--result", "addressed")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        before = self.env.run_path.read_text()
        r3 = self.council("r3.json", round=2, written_at=ts(3), decisions=(RC, OK, OK), findings=BLOCK)
        proc = self.cli("--outcome", "blocked", "--result", "ROUTE-3", "--artifacts", self.artifacts(held, r2, r3))
        self.assertEqual(self.out(proc)["gate"]["verdict"], "route", proc.stdout)
        after = self.env.run_path.read_text()
        self.assertIn("notes: |\n", after)
        self.assertNotIn('notes: "', after)
        self.assertTrue(self.env.load_run()["notes"].startswith(notes))
        self.assertIn("ROUTE-3", self.env.load_run()["notes"])
        # The whole earlier notes block, up to the key that follows it, is a byte prefix of the new one.
        cut = before.index("\nnotes: |\n")
        end = before.index("\npr_draft:", cut)
        self.assertTrue(after[after.index("\nnotes: |\n"):].startswith(before[cut:end]),
                        (before[cut:end], after))

    def test_a_whitespace_only_line_past_the_block_indent_is_content_and_the_route_succeeds(self):
        old = self.set_run_text("notes: |\n  prior note\n     \n")
        self.assertEqual(self.env.load_run()["notes"], "prior note\n   \n")
        self.route(result="AFTER WS")
        new = self.env.run_path.read_text()
        self.assertIn("notes: |\n  prior note\n     \n", new)
        self.assertNotIn('notes: "', new)
        notes = self.env.load_run()["notes"]
        self.assertTrue(notes.startswith("prior note\n   \n"), repr(notes))
        self.assertIn("AFTER WS", notes)
        self.assertTrue(old)

    def test_a_block_edit_that_fails_the_postcondition_refuses_and_never_re_encodes(self):
        text = self.set_run_text("notes: |\n  prior note\n")
        before = _ar.load_yaml(text)
        after = {**before, "notes": before["notes"] + "ENTRY\n"}
        broken = _ar._notes_block_edit
        calls = []

        def bad_edit(text, key_node, node, old, new, eol):
            calls.append(1)
            at = text.index("prior note\n") + len("prior note\n")
            return at, at, "  [unbalanced\n"  # re-reads as a different value

        _ar._notes_block_edit = bad_edit
        try:
            with contextlib.redirect_stdout(io.StringIO()) as buf, self.assertRaises(SystemExit) as cm:
                _ar._splice(text, before, after)
        finally:
            _ar._notes_block_edit = broken
        self.assertEqual(calls, [1])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("nothing was written", json.loads(buf.getvalue())["error"])
        # Positive control: the same splice with the real edit keeps the block layout.
        self.assertIn("notes: |\n  prior note\n  ENTRY\n", _ar._splice(text, before, after))

    def test_a_block_at_the_end_of_the_file_stays_a_block(self):
        cases = {"|+": ("notes: |+\n  prior note\n\n", True),
                 "|+ one final newline": ("notes: |+\n  prior note\n", True),
                 "|+ no final newline": ("notes: |+\n  prior note", False),
                 "| with newline": ("notes: |\n  prior note\n", True),
                 "| no final newline": ("notes: |\n  prior note", False)}
        for name, (notes_text, ends_nl) in cases.items():
            with self.subTest(case=name):
                self.set_run_text(notes_text, last=True)
                text = self.env.run_path.read_text()
                if not ends_nl:
                    self.assertFalse(text.endswith("\n"))
                head = notes_text.split("\n")[0]
                old_notes = self.env.load_run()["notes"]
                self.route(result="EOF ROUTE")
                new = self.env.run_path.read_text()
                self.assertIn("\n" + head + "\n  prior note", new, new[-300:])
                self.assertNotIn('notes: "', new)
                notes = self.env.load_run()["notes"]
                self.assertTrue(notes.startswith(old_notes.rstrip("\n")), repr(notes))
                self.assertIn("EOF ROUTE", notes)
                (self.env.council / "r1.json").unlink()


class GateInfoAndWithdrawnTests(_Case):
    BOOK = sup.make_book("adr", prompt_text={3: HISTORICAL})

    def setUp(self):
        super().setUp()
        self.env.set_run(3)

    def snapshot(self):
        return sha(self.env.run_path), sha(self.env.book_path)

    def test_gate_info_prints_the_correction_notice_and_writes_nothing(self):
        before = self.snapshot()
        proc = self.cli("--gate-info")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual((out["prompt"], out["class"], out["module_tag"], out["ordinal"]),
                         (3, "council", "adr-1", 2))
        self.assertIn("native-agents-equivalent", out["withdrawn"])
        self.assertIn("CORRECTION", out["correction_notice"])
        self.assertIn("run-council.py", out["requires"])
        self.assertEqual(self.snapshot(), before)

    def test_gate_info_for_a_prompt_without_the_alternative_has_no_notice(self):
        out = self.out(self.cli("--gate-info", "--prompt", "5"))
        self.assertEqual((out["class"], out["ordinal"], out["withdrawn"], out["correction_notice"]),
                         ("module-close", 4, [], None))
        out = self.out(self.cli("--gate-info", "--prompt", "10"))
        self.assertEqual(out["class"], "independent-review")
        self.assertIn("reviewer report", out["requires"])
        out = self.out(self.cli("--gate-info", "--prompt", "1"))
        self.assertEqual((out["class"], out["ordinal"]), ("unclassified", None))

    def test_gate_info_refuses_a_missing_prompt_and_a_clash_with_an_outcome(self):
        self.refused(self.cli("--gate-info", "--prompt", "99"), "no prompt 99")
        self.refused(self.cli("--gate-info", "--outcome", "done"), "read-only")
        self.refused(self.cli("--prompt", "3", "--outcome", "done"), "only valid with --gate-info")

    def test_gate_info_needs_a_resolvable_book(self):
        self.env.book_path.unlink()
        self.refused(self.cli("--gate-info"), "cannot resolve")

    def test_a_book_carrying_the_old_alternative_is_corrected_at_execution_and_its_bytes_stay(self):
        before = self.snapshot()
        proc = self.cli("--outcome", "done", "--result", "dispatched 5 parallel review agents")
        payload = self.refused(proc, "no council record")
        self.assertIn("native-agents-equivalent", payload["withdrawn"])
        self.assertIn("CORRECTION", payload["correction_notice"])
        self.assertEqual(self.snapshot(), before)
        # The book bytes stay identical after a passing advance too; only the run moves.
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correction_notice", self.out(proc))
        self.assertEqual(sha(self.env.book_path), before[1])

    def test_an_old_alternative_book_advanced_with_book_changes_only_its_current_prompt_line(self):
        before = self.env.book_path.read_text().splitlines()
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1),
                        "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertIn("native-agents-equivalent", out["withdrawn"])
        self.assertIn("CORRECTION", out["correction_notice"])
        after = self.env.book_path.read_text().splitlines()
        self.assertEqual(len(after), len(before))
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(changed), 1, changed)
        self.assertTrue(changed[0][0].startswith("current_prompt:"), changed)
        self.assertEqual(changed[0][1], "current_prompt: 4")
        self.assertIn(HISTORICAL[:40], "\n".join(after))

    def test_a_clean_book_carries_no_notice_on_an_advance(self):
        clean = sup.Env(Path(self._td.name) / "clean", kind="adr", autocommit=True)
        clean.set_run(3)
        r1 = clean.write("r1.json", clean.council_doc())
        proc = subprocess.run([sys.executable, str(ADVANCE), str(clean.run_path), "--outcome", "done",
                               "--artifacts", clean.rel(r1)], capture_output=True, text=True, cwd=clean.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["withdrawn"], out["correction_notice"]), ([], None))


class PatchGateTests(_Case):
    KIND = "patch"

    def test_a_patch_route_writes_nothing_and_exits_zero(self):
        r1 = self.council(decisions=(RC, OK, OK), findings=BLOCK)
        before = self.env.run_path.read_bytes()
        proc = self.cli("--outcome", "blocked", "--result", "findings", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual((out["gate"]["verdict"], out["gate"]["route_to"], out["written"]), ("route", 1, False))
        self.assertEqual((out["result"], out["artifacts"]), ("findings", [self.env.rel(r1)]))
        self.assertEqual(out["current_prompt"], 1)
        self.assertEqual(self.env.run_path.read_bytes(), before)

    def test_a_patch_converged_round_advances_and_a_patch_stop_blocks_in_place(self):
        held = self.council(decisions=(RC, OK, OK))
        proc = self.cli("--outcome", "blocked", "--artifacts", self.artifacts(held))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.states()[0][1], "blocked")
        self.assertEqual(self.states()[1], 1)
        # Deleting the held record and writing a converged round does not clear the stop.
        held.unlink()
        ok = self.council("ok.json", written_at=ts(2))
        stopped = self.env.run_path.read_bytes()
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(ok)), self.env.rel(held))
        self.assertEqual(self.env.run_path.read_bytes(), stopped)
        # Positive control: with the held record restored the stop stands, so the refusal was the deletion.
        sup.git(self.env.root, "checkout", "--", self.env.rel(held))
        proc = self.cli("--outcome", "blocked", "--artifacts", self.artifacts(held, ok))
        self.assertEqual(self.out(proc)["gate"]["stops"], [1])
        self.assertEqual(self.states()[1], 1)

    def test_a_patch_could_not_run_stop_clears_on_a_reconvened_round(self):
        cnr = self.council("cnr.json", outcome="could-not-run")
        proc = self.cli("--outcome", "blocked", "--artifacts", self.artifacts(cnr))
        self.assertEqual(self.out(proc)["gate"]["stops"], [4])
        ok = self.council("ok.json", written_at=ts(2))
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(cnr, ok))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.states()[1], 2)

    def test_the_patch_review_phase_is_an_independent_review_gate(self):
        self.env.set_run(4)
        council = self.env.write("c.json", self.env.council_doc(module_tag=None, prompt=4))
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(council)), "never satisfies")
        rep = self.env.write("rep.json", self.env.report_doc(prompt=4), folder=self.env.run_dir / "reviews")
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(rep))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class NonGatePathTests(_Case):
    def test_an_unclassified_prompt_advances_as_before_and_reports_its_class(self):
        self.env.set_run(1)
        proc = self.cli("--outcome", "done", "--result", "prep done")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = self.out(proc)
        self.assertEqual(out["gate"]["class"], "unclassified")
        self.assertNotIn("verdict", out["gate"])
        self.assertNotIn("withdrawn", out)
        self.assertEqual(self.states()[1], 2)

    def test_an_internal_review_prompt_advances_without_an_artifact(self):
        self.env.set_run(9)
        proc = self.cli("--outcome", "done", "--result", "self-review ok")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["gate"]["class"], "internal-review")
        self.env.set_run(9)
        proc = self.cli("--outcome", "blocked", "--result", "stuck")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_grandfathered_book_is_never_a_gate(self):
        book = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="old")
        env = sup.Env(Path(self._td.name) / "gf", kind="adr", book=book, autocommit=True,
                      base=False)
        pin_base(env, 3)
        proc = subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "done"],
                              capture_output=True, text=True, cwd=env.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["gate"]["class"], "unclassified")

    def test_a_non_cycle_book_is_never_a_gate(self):
        book = sup.make_book("adr", cycle_kind=None)
        env = sup.Env(Path(self._td.name) / "nc", kind="adr", book=book, autocommit=True,
                      base=False)
        pin_base(env, 3)
        proc = subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "done"],
                              capture_output=True, text=True, cwd=env.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["gate"]["class"], "unclassified")


class BookResolutionTests(_Case):
    def test_an_unresolvable_book_refuses_outcome_but_not_abandon(self):
        self.env.set_run(1)
        before = self.env.run_path.read_bytes()
        self.env.book_path.unlink()
        self.refused(self.cli("--outcome", "done"), "cannot resolve the run's book")
        self.assertEqual(self.env.run_path.read_bytes(), before)
        proc = self.cli("--abandon", "--reason", "book is gone")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.states()[2], "abandoned")

    def test_a_hash_mismatch_refuses_the_advance_and_a_match_advances(self):
        self.env.set_run(1)
        before = self.env.run_path.read_bytes()
        original = self.env.book_path.read_text()
        self.env.book_path.write_text(original.replace("title: Fixture", "title: Changed"))
        self.refused(self.cli("--outcome", "done"), "content hash")
        self.assertEqual(self.env.run_path.read_bytes(), before)
        self.env.book_path.write_text(original)
        self.assertEqual(self.cli("--outcome", "done").returncode, 0)

    def test_the_archive_is_a_fallback_for_a_book_already_archived(self):
        self.env.set_run(1)
        archive = self.env.docs / "promptbooks" / "archive"
        archive.mkdir()
        self.env.book_path.rename(archive / self.env.book_path.name)
        proc = self.cli("--outcome", "done")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_symlinked_book_refuses_the_advance_through_the_cli(self):
        self.env.set_run(1)
        before = self.env.run_path.read_bytes()
        real = Path(self._td.name) / "real-book.yaml"
        real.write_bytes(self.env.book_path.read_bytes())
        self.env.book_path.unlink()
        self.env.book_path.symlink_to(real)
        self.refused(self.cli("--outcome", "done"), "symlink")
        self.assertEqual(self.env.run_path.read_bytes(), before)
        # Positive control: the book as a regular file advances the same run.
        self.env.book_path.unlink()
        self.env.book_path.write_bytes(real.read_bytes())
        self.assertEqual(self.cli("--outcome", "done").returncode, 0)

    def test_a_layout_resolved_book_is_never_written(self):
        self.env.set_run(1)
        before = self.env.book_path.read_bytes()
        self.assertEqual(self.cli("--outcome", "done").returncode, 0)
        self.assertEqual(self.env.book_path.read_bytes(), before)

    def test_only_an_explicit_book_has_its_pointer_rewritten(self):
        self.env.set_run(1)
        proc = self.cli("--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("current_prompt: 2", self.env.book_path.read_text())

    def test_an_explicit_book_with_the_wrong_hash_is_refused(self):
        self.env.set_run(1)
        other = Path(self._td.name) / "other.yaml"
        other.write_text("id: PB-0999\ncurrent_run: RUN-001\ncurrent_prompt: 1\n")
        before = self.env.run_path.read_bytes()
        self.refused(self.cli("--outcome", "done", "--book", str(other)), "content hash")
        self.assertEqual(self.env.run_path.read_bytes(), before)

    def test_a_gate_refusal_leaves_an_explicit_book_untouched_too(self):
        self.env.set_run(3)
        before = (self.env.run_path.read_bytes(), self.env.book_path.read_bytes())
        self.refused(self.cli("--outcome", "done", "--book", str(self.env.book_path)), "no council record")
        self.assertEqual((self.env.run_path.read_bytes(), self.env.book_path.read_bytes()), before)


class MainEntryPointTests(_Case):
    def main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                code = _ar.main([str(self.env.run_path), *args])
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(buf.getvalue())

    def test_main_refuses_a_gate_without_a_record_and_passes_with_one(self):
        self.env.set_run(3)
        before = self.env.run_path.read_bytes()
        code, out = self.main("--outcome", "done", "--result", "council approved (5/5 reviewers)")
        self.assertEqual(code, 1)
        self.assertEqual(out["gate"]["verdict"], "refuse")
        self.assertEqual(self.env.run_path.read_bytes(), before)
        r1 = self.council()
        code, out = self.main("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(code, 0)
        self.assertEqual(out["gate"]["deciding_record"], self.env.rel(r1))

    def test_main_gate_info_returns_zero(self):
        self.env.set_run(3)
        code, out = self.main("--gate-info")
        self.assertEqual((code, out["class"]), (0, "council"))


# ───────────────────────────── PB-0136 internal-review fixes ─────────────────────────────


class UnboundCycleFieldTests(_Case):
    """`cycle_grandfathered` and `cycle_kind` lie outside the content hash. An in-flight edit to
    either is refused; the run-start book (at `base_commit`) decides the class."""

    BASE = False  # each case pins its own base_commit

    def edit_book(self, change):
        book = sup.yaml.safe_load(self.env.book_path.read_text())
        change(book)
        self.env.book_path.write_text(sup.yaml.safe_dump(book, sort_keys=False))
        self.assertEqual(sup.vp.compute_book_hash(book), self.env.hash)  # the hash still verifies

    def snapshot(self):
        return self.env.run_path.read_bytes(), self.env.book_path.read_bytes()

    def test_adding_cycle_grandfathered_mid_run_is_refused(self):
        pin_base(self.env, 3)
        self.edit_book(lambda b: b.update(cycle_grandfathered=True, grandfather_reason="bypass"))
        before = self.snapshot()
        self.refused(self.cli("--outcome", "done", "--result", "no record"), "cycle_grandfathered")
        self.assertEqual(self.snapshot(), before)
        self.refused(self.cli("--outcome", "done", "--book", str(self.env.book_path)), "cycle_grandfathered")
        self.assertEqual(self.snapshot(), before)
        self.refused(self.cli("--gate-info"), "cycle_grandfathered")

    def test_deleting_cycle_kind_mid_run_is_refused(self):
        pin_base(self.env, 3)
        self.edit_book(lambda b: b.pop("cycle_kind"))
        before = self.snapshot()
        self.refused(self.cli("--outcome", "done", "--result", "no record"), "cycle_kind")
        self.assertEqual(self.snapshot(), before)

    def test_an_unedited_book_with_a_pinned_start_keeps_its_gate(self):
        # The positive control for both bypasses: the same run, book unedited.
        pin_base(self.env, 3)
        payload = self.refused(self.cli("--outcome", "done"), "no council record")
        self.assertEqual(payload["gate"]["cycle_fields"], "run-start")
        r1 = self.council()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_committed_rewrite_of_base_commit_cannot_rebind_the_start_fields(self):
        # Run start: the snapshot's first committed version records base A, where the book has
        # its gates. The book is then grandfathered at B (the field lies outside the hash), and
        # base_commit is rewritten to B in a commit, so the HEAD-pinned check sees no divergence.
        pin_base(self.env, 3)
        sup.commit_all(self.env.root, "run start")
        self.edit_book(lambda b: b.update(cycle_grandfathered=True, grandfather_reason="bypass"))
        sup.commit_all(self.env.root, "grandfather the book")
        later = sup.git(self.env.root, "rev-parse", "HEAD").strip()
        run = self.env.load_run()
        run["base_commit"] = later
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        sup.commit_all(self.env.root, "rewrite base_commit")
        before = self.snapshot()
        for args in (("--outcome", "done", "--result", "no record"), ("--gate-info",)):
            with self.subTest(args=args):
                self.refused(self.cli(*args), "first committed version")
                self.assertEqual(self.snapshot(), before)

    def test_a_committed_snapshot_with_its_run_start_base_keeps_its_gate(self):
        # The positive control for the rewrite case: committed, base_commit untouched.
        pin_base(self.env, 3)
        sup.commit_all(self.env.root, "run start")
        payload = self.refused(self.cli("--outcome", "done"), "no council record")
        self.assertEqual(payload["gate"]["cycle_fields"], "run-start")
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(self.council()))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_book_grandfathered_at_run_start_stays_unclassified(self):
        book = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="old")
        env = sup.Env(Path(self._td.name) / "gf", kind="adr", book=book, autocommit=True,
                      base=False)
        pin_base(env, 3)
        proc = subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "done"],
                              capture_output=True, text=True, cwd=env.root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["gate"]["class"], "unclassified")

    def test_an_unknown_run_start_classifies_from_the_hash_bound_fields(self):
        book = sup.make_book("adr", cycle_grandfathered=True, grandfather_reason="old")
        env = sup.Env(Path(self._td.name) / "gf", kind="adr", book=book, autocommit=True,
                      base=False)

        def advance():
            return subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "done"],
                                  capture_output=True, text=True, cwd=env.root)

        # No base_commit: the flag the run did not bind is ignored and the gate holds.
        env.set_run(3)
        before = env.run_path.read_bytes()
        proc = advance()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["gate"]["class"], out["gate"]["cycle_fields"]), ("council", "unbound"))
        self.assertEqual(env.run_path.read_bytes(), before)
        # A base_commit at which the book did not exist is unknown too.
        sup.git(env.root, "rm", "-q", "--cached", env.rel(env.book_path))
        sup.git(env.root, "commit", "-q", "-m", "drop book")
        no_book = sup.git(env.root, "rev-parse", "HEAD").strip()
        sup.commit_all(env.root, "restore book")
        pin_base(env, 3, base=no_book)
        self.assertEqual(advance().returncode, 1)
        # Positive control: a base_commit that holds the grandfathered book leaves it unclassified.
        pin_base(env, 3)
        proc = advance()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["gate"]["class"], "unclassified")


class SymlinkedPathTests(_Case):
    """A run snapshot or an explicit book reached through a symlink, at the leaf or at an
    ancestor below the repository root, is refused with nothing written."""

    def setUp(self):
        super().setUp()
        self.env.set_run(1)

    def bytes_(self):
        return self.env.run_path.read_bytes(), self.env.book_path.read_bytes()

    def run_cli(self, run, *args):
        return subprocess.run([sys.executable, str(ADVANCE), str(run), *args],
                              capture_output=True, text=True, cwd=self.env.root, env=sup.scrubbed_env())

    def test_a_symlinked_run_snapshot_leaf_is_refused(self):
        link = self.env.run_dir / "run-RUN-009.yaml"
        link.symlink_to(self.env.run_path.name)
        before = self.bytes_()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r"), ("--gate-info",)):
            with self.subTest(args=args):
                self.refused(self.run_cli(link, *args), "symlink")
                self.assertEqual(self.bytes_(), before)
        # Positive control: the real path advances.
        self.assertEqual(self.run_cli(self.env.run_path, "--outcome", "done").returncode, 0)

    def test_a_run_snapshot_under_a_symlinked_ancestor_is_refused(self):
        alias = self.env.docs / "promptbooks" / "runs-alias"
        alias.symlink_to("runs")
        via = alias / self.env.name / self.env.run_path.name
        self.assertTrue(via.is_file())
        before = self.bytes_()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r")):
            with self.subTest(args=args):
                self.refused(self.run_cli(via, *args), "symlink")
                self.assertEqual(self.bytes_(), before)
        self.assertEqual(self.run_cli(self.env.run_path, "--outcome", "done").returncode, 0)

    def test_an_explicit_book_with_a_symlinked_leaf_is_refused_for_every_mode(self):
        link = self.env.book_path.with_name("book-link.yaml")
        link.symlink_to(self.env.book_path.name)
        before = self.bytes_()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r"), ("--gate-info",)):
            with self.subTest(args=args):
                self.refused(self.run_cli(self.env.run_path, *args, "--book", str(link)), "symlink")
                self.assertEqual(self.bytes_(), before)
        proc = self.run_cli(self.env.run_path, "--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_explicit_book_under_a_symlinked_ancestor_is_refused(self):
        alias = self.env.docs / "promptbooks" / "active-alias"
        alias.symlink_to("active")
        via = alias / self.env.book_path.name
        before = self.bytes_()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r")):
            with self.subTest(args=args):
                self.refused(self.run_cli(self.env.run_path, *args, "--book", str(via)), "symlink")
                self.assertEqual(self.bytes_(), before)
        proc = self.run_cli(self.env.run_path, "--abandon", "--reason", "r", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_symlink_refusal_names_its_remedy(self):
        # A false refusal (a checkout reached through a link held in an enclosing repository)
        # has one remedy, so the message names it.
        alias = self.env.docs / "promptbooks" / "runs-alias"
        alias.symlink_to("runs")
        via = alias / self.env.name / self.env.run_path.name
        payload = self.refused(self.run_cli(via, "--gate-info"), "symlink")
        self.assertIn("by its physical path", payload["error"])

    # An in-repo symlink whose target lies OUTSIDE the repository. The physical parent of such a
    # path has another repository root, or none, so a walk anchored there never sees the link.

    def _outside(self, *, git_repo: bool = False) -> Path:
        """A directory outside the fixture repository; a git work-tree root when `git_repo`."""
        target = Path(self._td.name).resolve() / ("other-repo" if git_repo else "outside")
        if git_repo:
            sup.init_repo(target)
        else:
            target.mkdir()
        return target

    def test_a_run_snapshot_under_an_in_repo_link_to_a_directory_outside_any_repository_is_refused(self):
        outside = self._outside()
        stolen = outside / self.env.run_path.name
        stolen.write_bytes(self.env.run_path.read_bytes())
        link = self.env.docs / "promptbooks" / "runs" / "evil"
        link.symlink_to(outside)
        via = link / self.env.run_path.name
        before, outside_before = self.bytes_(), stolen.read_bytes()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r"), ("--gate-info",)):
            with self.subTest(args=args):
                self.refused(self.run_cli(via, *args, "--book", str(self.env.book_path)), "symlink")
                self.assertEqual(self.bytes_(), before)
                self.assertEqual(stolen.read_bytes(), outside_before)
        # Positive controls. The copy outside the repository is a working snapshot: named by its
        # real path, which leaves every repository, it advances. So the refusal above is the link's.
        proc = self.run_cli(stolen, "--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotEqual(stolen.read_bytes(), outside_before)
        proc = self.run_cli(self.env.run_path, "--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_run_snapshot_under_an_in_repo_link_to_another_repository_root_is_refused(self):
        other = self._outside(git_repo=True)
        stolen = other / self.env.run_path.name
        stolen.write_bytes(self.env.run_path.read_bytes())
        link = self.env.docs / "promptbooks" / "runs" / "evil"
        link.symlink_to(other)
        before, outside_before = self.bytes_(), stolen.read_bytes()
        proc = self.run_cli(link / stolen.name, "--outcome", "done", "--book", str(self.env.book_path))
        self.refused(proc, "symlink")
        self.assertEqual((self.bytes_(), stolen.read_bytes()), (before, outside_before))
        proc = self.run_cli(stolen, "--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_run_snapshot_reached_by_a_parent_step_out_of_an_in_repo_link_is_refused(self):
        # `runs/evil/../<dir>/run.yaml` names the real snapshot lexically, and a file outside
        # the repository physically: the link is followed before `..` is applied.
        outside = self._outside() / "deep"
        outside.mkdir()
        decoy_dir = outside.parent / self.env.name
        decoy_dir.mkdir()
        decoy = decoy_dir / self.env.run_path.name
        decoy.write_bytes(self.env.run_path.read_bytes())
        link = self.env.docs / "promptbooks" / "runs" / "evil"
        link.symlink_to(outside)
        via = Path(f"{link}/../{self.env.name}/{self.env.run_path.name}")
        self.assertEqual(Path(os.path.realpath(via)), decoy.resolve())
        before, decoy_before = self.bytes_(), decoy.read_bytes()
        proc = self.run_cli(via, "--outcome", "done", "--book", str(self.env.book_path))
        self.refused(proc, "symlink")
        self.assertEqual((self.bytes_(), decoy.read_bytes()), (before, decoy_before))
        proc = self.run_cli(decoy, "--outcome", "done", "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_an_explicit_book_under_an_in_repo_link_to_a_directory_outside_any_repository_is_refused(self):
        outside = self._outside()
        copy = outside / self.env.book_path.name
        copy.write_bytes(self.env.book_path.read_bytes())
        link = self.env.docs / "promptbooks" / "active-out"
        link.symlink_to(outside)
        via = link / copy.name
        before, copy_before = self.bytes_(), copy.read_bytes()
        for args in (("--outcome", "done"), ("--abandon", "--reason", "r"), ("--gate-info",)):
            with self.subTest(args=args):
                self.refused(self.run_cli(self.env.run_path, *args, "--book", str(via)), "symlink")
                self.assertEqual((self.bytes_(), copy.read_bytes()), (before, copy_before))
        # Positive control: the same book named by its real path outside every repository binds,
        # and the run advances from prompt 1 to prompt 2.
        proc = self.run_cli(self.env.run_path, "--outcome", "done", "--book", str(copy))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.env.load_run()["current_prompt"], 2)


class UsageNamesTheBookRequirementTests(unittest.TestCase):
    def test_the_docstring_and_help_say_every_outcome_needs_a_hash_matched_book(self):
        doc = _ar.__doc__
        self.assertIn("Every `--outcome` needs a resolvable book whose content hash matches", doc)
        proc = subprocess.run([sys.executable, str(ADVANCE), "--help"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("every --outcome needs a resolvable book", " ".join(proc.stdout.split()))


class CommittedEvidenceCliTests(_Case):
    """The gate reads only committed records, and a record committed in HEAD cannot be removed or
    changed to clear a stop (M5). Each refusal writes nothing and is paired with its passing control."""

    def setUp(self):
        super().setUp()
        self.env.set_run(3)
        self.before = self.env.run_path.read_bytes()

    def assert_run_unchanged(self):
        self.assertEqual(self.env.run_path.read_bytes(), self.before)

    def test_an_uncommitted_record_is_refused_and_the_committed_one_passes(self):
        r1 = self.env.write("r1.json", self.env.council_doc(), commit=False)
        payload = self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r1)),
                               "commit the record before the advance")
        # A format-1 record: recovery cannot resolve one, so the remedy is the conductor's commit.
        self.assertNotIn("run-council.py --recover", payload["error"])
        self.assertEqual(payload["gate"]["verdict"], "refuse")
        self.assert_run_unchanged()
        self.env.commit_records(r1)
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_committed_record_deleted_or_modified_is_refused_and_restoring_it_passes(self):
        r1 = self.council("r1.json", written_at=ts(1), decisions=(RC, OK, OK), findings=BLOCK)
        r2 = self.council("r2.json", round=2, written_at=ts(2))
        rel1 = self.env.rel(r1)
        r1.unlink()
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r2)), "committed in HEAD")
        self.assert_run_unchanged()
        sup.git(self.env.root, "checkout", "--", rel1)
        doc = json.loads(r1.read_text())
        doc["seats"][0]["confidence"] = 0.1
        r1.write_text(json.dumps(doc, indent=2) + "\n")
        payload = self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r1, r2)),
                               "is not committed")
        self.assertIn(rel1, payload["error"])
        self.assert_run_unchanged()
        sup.git(self.env.root, "checkout", "--", rel1)
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1, r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_snapshot_named_record_that_is_missing_is_refused(self):
        r1 = self.council("r1.json")
        run = self.env.load_run()
        run["prompts"][2]["artifacts"] = [self.env.rel(self.env.council / "ghost.json")]
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        self.before = self.env.run_path.read_bytes()
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(r1)),
                     "names a record that is missing")
        self.assert_run_unchanged()
        run["prompts"][2]["artifacts"] = [self.env.rel(r1)]  # control: the named record exists
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r1))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RegistryCliTests(_Case):
    """A council record seated off the current router registry is refused at advance (M6)."""

    def setUp(self):
        super().setUp()
        self.env.set_run(3)
        self.before = self.env.run_path.read_bytes()

    def record(self, name="r1.json", **changes):
        doc = self.env.council_doc()
        doc["seats"][0].update(changes)
        return self.env.write(name, doc)

    def test_an_unregistered_model_is_refused_and_the_registered_one_passes(self):
        ghost = self.record(registry_key="gpt-9-ghost", requested_model="openai/gpt-9-ghost",
                            served_model="openai/gpt-9-ghost")
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(ghost)), "the registry assigns")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        ok = self.record()
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(ok))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_served_model_the_registry_does_not_accept_is_refused(self):
        bad = self.record(served_model="openai/gpt-6.1-sol")
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(bad)), "served_model")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        ok = self.record()
        self.assertEqual(self.cli("--outcome", "done", "--artifacts", self.artifacts(ok)).returncode, 0)


class RegistryRotationCliTests(_Case):
    """A round seated on a rotated assignment does not strand the module: the registry check reads
    the deciding record only."""

    def setUp(self):
        super().setUp()
        self.env.set_run(3)

    def test_a_converged_round_on_the_current_assignment_passes_after_an_older_rotated_round(self):
        r1 = self.env.council_doc(round=1, written_at=sup.ts(1),
                                  decisions=("REQUEST_CHANGES", "APPROVE", "APPROVE"),
                                  findings={"openai_top": [("F1", "Correctness", False, "blocking")]})
        r1["seats"][0].update(registry_key="gpt-6-sol", requested_model="openai/gpt-6-sol",
                              served_model="openai/gpt-6-sol")
        self.env.write("r1.json", r1)
        r2 = self.env.write("r2.json", self.env.council_doc(round=2, written_at=sup.ts(2)))
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(r2))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.env.load_run()["current_prompt"], 4)


class ReviewGateArtifactCliTests(_Case):
    """An attached artifact that is no reviewer report is refused, never skipped (M3)."""

    def setUp(self):
        super().setUp()
        self.env.set_run(10)
        self.before = self.env.run_path.read_bytes()

    def test_a_non_json_artifact_beside_a_valid_report_is_refused(self):
        rep = self.env.write("rep.json", self.env.report_doc(prompt=10), folder=self.env.run_dir / "reviews")
        notes = self.env.root / "notes.md"
        notes.write_text("# notes\n")
        self.refused(self.cli("--outcome", "done", "--artifacts", self.artifacts(rep, notes)),
                     "is not a reviewer report (.json)")
        self.assertEqual(self.env.run_path.read_bytes(), self.before)
        proc = self.cli("--outcome", "done", "--artifacts", self.artifacts(rep))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class GateInfoRequiresTextTests(_Case):
    """`--gate-info` states what the gate reads: the newest record decides, and authorship is not verified."""

    def requires(self, n):
        proc = self.cli("--gate-info", "--prompt", str(n))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return " ".join(self.out(proc)["requires"].split())

    def test_the_council_and_close_text_names_the_deciding_record_and_the_self_asserted_writer(self):
        council, close = self.requires(3), self.requires(5)
        for text in (council, close):
            self.assertIn("a committed council record that names run-council.py as its writer "
                          "(a self-asserted field)", text)
            self.assertIn("only the seats of the record that decides (the deciding council record, or "
                          "the council record a deciding refutation names) are checked against the "
                          "current router registry assignment", text)
            self.assertIn("attach the deciding record, council or refutation, committed, with --artifacts",
                          text)
            self.assertIn("committed last", text)
            self.assertNotIn("the latest record decides", text)
            self.assertNotIn("only convergence closes the gate", text)
        self.assertIn("the record committed last decides: it converges, or in an adr module a "
                      "refutation record shows every blocking finding refuted", council)
        self.assertIn("the deciding record is the one committed last: a council record, or in an adr "
                      "module a refutation record", close)
        self.assertIn("run-council.py", close)

    def test_the_review_text_is_unchanged_by_the_council_wording(self):
        self.assertIn("a reviewer report", self.requires(10))

    def test_the_review_text_says_a_non_json_artifact_is_refused(self):
        self.assertIn("an attached artifact that is not a .json reviewer report is refused", self.requires(10))


class GateInfoAndRouteCoverageTests(_Case):
    def snapshot(self):
        return sha(self.env.run_path), sha(self.env.book_path)

    def test_gate_info_with_a_null_current_prompt_needs_an_explicit_prompt(self):
        run = self.env.load_run()
        run["current_prompt"] = None
        run["status"] = "completed"
        self.env.run_path.write_text(sup.yaml.safe_dump(run, sort_keys=False))
        before = self.snapshot()
        payload = self.refused(self.cli("--gate-info"), "current_prompt: null; pass --prompt N")
        self.assertNotIn("class", payload)
        # Positive control: the same run answers with an explicit prompt.
        proc = self.cli("--gate-info", "--prompt", "3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.out(proc)["class"], "council")
        self.assertEqual(self.snapshot(), before)

    def test_a_route_in_a_module_with_no_ordinal_three_prompt_refuses_and_writes_nothing(self):
        book = sup.make_book("adr")
        book["prompts"] = [p for p in book["prompts"] if p["n"] not in (4, 5)]
        env = sup.Env(Path(self._td.name) / "short", kind="adr", book=book, autocommit=True)
        env.set_run(3)
        r1 = env.write("r1.json", env.council_doc(decisions=(RC, OK, OK), findings=BLOCK))
        before = env.run_path.read_bytes()
        proc = subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "blocked",
                               "--artifacts", env.rel(r1)], capture_output=True, text=True, cwd=env.root,
                              env=sup.scrubbed_env())
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("the module has no ordinal-3 prompt to route to; nothing was written", payload["error"])
        self.assertEqual(env.run_path.read_bytes(), before)
        # Positive control: a converged round in the same module closes the gate.
        r2 = env.write("r2.json", env.council_doc(round=2, written_at=ts(2)))
        proc = subprocess.run([sys.executable, str(ADVANCE), str(env.run_path), "--outcome", "done",
                               "--artifacts", env.rel(r1) + "," + env.rel(r2)], capture_output=True,
                              text=True, cwd=env.root, env=sup.scrubbed_env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class ExplicitBookAdvanceChangesOnlyWhatItMustTests(_Case):
    """An advance with --book changes the run's current prompt and the advanced prompt's own
    fields, and the book's `current_prompt` line: nothing else (the book is never rewritten)."""

    def test_an_advance_with_book_changes_only_the_documented_fields(self):
        self.env.set_run(3)
        r1 = self.council()
        book_before = self.env.book_path.read_text()
        run_before = self.env.load_run()
        proc = self.cli("--outcome", "done", "--result", "converged", "--artifacts", self.artifacts(r1),
                        "--book", str(self.env.book_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # The book: exactly one line differs, and it is the `current_prompt` line.
        old, new = book_before.splitlines(), self.env.book_path.read_text().splitlines()
        self.assertEqual(len(old), len(new))
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(changed), 1, changed)
        self.assertTrue(changed[0][0].startswith("current_prompt: "), changed)
        self.assertEqual(changed[0][1], "current_prompt: 4")
        # The run: top level except the pointer, and every prompt but 3 and 4, are untouched.
        run_after = self.env.load_run()
        self.assertEqual({k: v for k, v in run_after.items() if k not in ("current_prompt", "prompts")},
                         {k: v for k, v in run_before.items() if k not in ("current_prompt", "prompts")})
        self.assertEqual(run_after["current_prompt"], 4)
        for before, after in zip(run_before["prompts"], run_after["prompts"]):
            if before["n"] not in (3, 4):
                self.assertEqual(before, after, before["n"])
        p3b, p3a = run_before["prompts"][2], run_after["prompts"][2]
        self.assertEqual({k: v for k, v in p3b.items() if k not in ("state", "completed", "result", "artifacts")},
                         {k: v for k, v in p3a.items() if k not in ("state", "completed", "result", "artifacts")})
        self.assertEqual((p3a["state"], p3a["result"], p3a["artifacts"]),
                         ("done", "converged", [self.env.rel(r1)]))
        p4b, p4a = run_before["prompts"][3], run_after["prompts"][3]
        self.assertEqual({k: v for k, v in p4b.items() if k not in ("state", "started")},
                         {k: v for k, v in p4a.items() if k not in ("state", "started")})
        self.assertEqual((p4b["state"], p4a["state"]), ("pending", "running"))
        # The record the advance read is untouched.
        self.assertEqual(sup.git(self.env.root, "status", "--porcelain", "--", self.env.rel(r1)), "")


if __name__ == "__main__":
    unittest.main()
